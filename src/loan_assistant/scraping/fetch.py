"""Polite fetching with a raw cache and a manifest, plus the scrape orchestration."""

import argparse
import csv
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from loan_assistant.config import settings
from loan_assistant.scraping import discover

log = logging.getLogger(__name__)

MAX_REDIRECTS = 5
REDIRECTS = (301, 302, 303, 307, 308)
OFFSITE_REDIRECT = 301  # recorded when a page redirects off the official host; final, not retried
MANIFEST_FIELDS = ["url", "type", "http_status", "fetched_at", "bytes", "sha256", "path"]


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    from_cache: bool


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = settings.user_agent
    retry = Retry(
        total=settings.max_retries,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


class Fetcher:
    """GET with robots.txt check, >= 1 s spacing, retries, on-disk cache and a manifest."""

    def __init__(self, raw_dir: Path, session: requests.Session | None = None, refresh=False):
        self.raw_dir = raw_dir
        self.session = session or make_session()
        self.refresh = refresh
        self.requests_made = 0
        self._last_request = 0.0
        self._robots: RobotFileParser | None = None
        self.manifest_path = raw_dir / "manifest.csv"
        self.manifest: dict[str, dict] = self._load_manifest()

    # -- manifest -------------------------------------------------------------------------
    def _load_manifest(self) -> dict[str, dict]:
        if not self.manifest_path.exists():
            return {}
        with self.manifest_path.open(newline="", encoding="utf-8") as f:
            return {r["url"]: r for r in csv.DictReader(f)}

    def _save_manifest(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            w.writeheader()
            w.writerows(sorted(self.manifest.values(), key=lambda r: r["url"]))

    # -- paths ----------------------------------------------------------------------------
    def cache_path(self, url: str, kind: str) -> Path:
        ext = {"html": "html", "pdf": "pdf", "xml": "xml", "txt": "txt"}[kind]
        folder = "html" if kind in ("html", "xml", "txt") else "pdf"
        return self.raw_dir / folder / f"{discover.slugify(url)}.{ext}"

    # -- robots ---------------------------------------------------------------------------
    def _load_robots(self) -> RobotFileParser:
        if self._robots is None:
            rp = RobotFileParser()
            resp = self._http_get(f"{settings.base_url}/robots.txt")
            rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            self._robots = rp
        return self._robots

    def allowed(self, url: str) -> bool:
        return self._load_robots().can_fetch(settings.user_agent, url)

    # -- http -----------------------------------------------------------------------------
    def _http_get(self, url: str) -> requests.Response:
        """GET that follows redirects on the official host only (max 5 hops).

        A redirect to any other host (the site still sends some pages to the retired
        bankofmaharashtra.in) is not followed; the 3xx response is returned as is.
        """
        for _ in range(MAX_REDIRECTS + 1):
            wait = settings.request_delay_s - (time.monotonic() - self._last_request)
            if wait > 0:
                log.info("waiting %.1fs before %s", wait, url)
                time.sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1
            resp = self.session.get(url, timeout=settings.request_timeout_s, allow_redirects=False)
            location = resp.headers.get("Location") if resp.status_code in REDIRECTS else None
            if not location:
                return resp
            target = urljoin(url, location)
            if urlparse(target).netloc != discover.HOST:
                log.warning("not following redirect off the official host: %s -> %s", url, target)
                return resp
            url = target
        return resp

    def get(self, url: str, kind: str = "html") -> FetchResult:
        """Return the cached copy when we have a final answer for it, else fetch and record."""
        path = self.cache_path(url, kind)
        row = self.manifest.get(url)
        if row and not self.refresh:
            if row["http_status"] == "200" and path.exists():
                return FetchResult(url, 200, path.read_bytes(), True)
            if row["http_status"] == str(OFFSITE_REDIRECT):
                return FetchResult(url, OFFSITE_REDIRECT, b"", True)
        if kind != "xml" and url != f"{settings.base_url}/robots.txt" and not self.allowed(url):
            log.warning("robots.txt disallows %s; skipping", url)
            self._record(url, kind, 403, b"", None)
            return FetchResult(url, 403, b"", False)
        try:
            resp = self._http_get(url)
        except requests.RequestException as exc:
            log.error("request failed for %s: %s", url, type(exc).__name__)
            self._record(url, kind, 0, b"", None)
            return FetchResult(url, 0, b"", False)
        content = resp.content if resp.status_code == 200 else b""
        if content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self._record(url, kind, resp.status_code, content, path if content else None)
        return FetchResult(url, resp.status_code, content, False)

    def _record(self, url, kind, status, content, path) -> None:
        self.manifest[url] = {
            "url": url,
            "type": kind,
            "http_status": str(status),
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "bytes": str(len(content)),
            "sha256": hashlib.sha256(content).hexdigest() if content else "",
            "path": str(path.relative_to(self.raw_dir)) if path else "",
        }
        self._save_manifest()


def collect_sitemap_urls(fetcher: Fetcher, max_files: int = 20) -> list[str]:
    """Walk sitemap.xml (and nested sitemaps) and return all page URLs."""
    queue = [f"{settings.base_url}/sitemap.xml"]
    pages: list[str] = []
    seen: set[str] = set()
    while queue and len(seen) < max_files:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        res = fetcher.get(url, kind="xml")
        if res.status != 200:
            log.warning("sitemap %s returned %s", url, res.status)
            continue
        found, nested = discover.parse_sitemap(res.content.decode("utf-8", "replace"))
        pages += found
        queue += [n for n in nested if urlparse(n).netloc == discover.HOST]
    return pages


def run_scrape(refresh: bool = False) -> dict:
    """Discover URLs, fetch every page and linked PDF, and return a short summary."""
    from loan_assistant.scraping import pdf

    raw = settings.raw_dir
    fetcher = Fetcher(raw, refresh=refresh)
    sitemap_urls = collect_sitemap_urls(fetcher)

    # Pass 1: hubs + sitemap matches. Pass 2: loan links found on those pages.
    records = discover.build_url_list(sitemap_urls)
    extra: list[tuple[str, str]] = []
    for rec in (r for r in records if r.source == "seed"):  # links are read from hubs only
        res = fetcher.get(rec.url)
        if res.status == 200:
            extra += [
                (u, "link")
                for u in discover.extract_links(res.content.decode("utf-8", "replace"), rec.url)
            ]
    records = discover.build_url_list(sitemap_urls, extra)
    for rec in records:
        fetcher.get(rec.url)

    pdf_summary = pdf.fetch_linked_pdfs(fetcher, [r.url for r in records])
    discover.write_urls_csv(records, raw / "urls.csv")

    def status_of(url: str) -> str:
        return fetcher.manifest.get(url, {}).get("http_status", "")

    statuses = [
        fetcher.manifest[r.url]["http_status"] for r in records if r.url in fetcher.manifest
    ]
    return {
        "urls": len(records),
        "tier1": sum(r.tier == 1 for r in records),
        "ok_200": statuses.count("200"),
        "offsite_redirects": [r.url for r in records if status_of(r.url) == str(OFFSITE_REDIRECT)],
        "failed": [
            r.url for r in records if status_of(r.url) not in ("200", str(OFFSITE_REDIRECT))
        ],
        "pdfs": pdf_summary,
        "tier1_slugs_not_found": discover.missing_tier1(records),
        "network_requests": fetcher.requests_made,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape BoM loan pages (official domain only).")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and re-fetch")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_scrape(refresh=args.refresh)
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
