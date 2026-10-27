"""Clean every scraped page and build the knowledge base.

Reads ``data/raw`` (urls.csv, manifest.csv, html/, pdf/) and writes, under ``data/processed``:
``pages/<slug>.md`` (one cleaned page each), ``knowledge_base.md`` (all pages, readable),
``loan_products.jsonl`` (one record per page) and ``stats.json`` (what was kept and dropped).

Run: ``python -m loan_assistant.processing.consolidate [--include msme,agri | --include none]``
All loan scopes (retail, MSME, agriculture) are included by default, as clarified.
"""

import argparse
import csv
import json
import logging
import math
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from loan_assistant.config import settings
from loan_assistant.processing.clean import (
    CleanPage,
    Section,
    clean_html,
    clean_pdf_text,
)
from loan_assistant.processing.pdf_text import pdf_to_text
from loan_assistant.scraping import discover

log = logging.getLogger(__name__)

BOILERPLATE_SHARE = 0.5  # a line on at least this share of pages is boilerplate (spec FR-B5)
MIN_LINE_WORDS = 4  # shorter lines (list labels, "Yes") are too generic to judge
THIN_PAGE_WORDS = 200
PDF_MAX_LINKING_PAGES = 3  # a PDF linked from most pages is a footer link, not product content
PDF_WORDS = (50, 6000)
CATEGORY_ORDER = [
    "home",
    "personal",
    "vehicle",
    "education",
    "gold",
    "property",
    "other",
    "rates",
    "charges",
    "msme",
    "agri",
]
TIER2 = {"msme", "agri"}


@dataclass
class Page:
    slug: str
    title: str
    url: str
    category: str
    tier: int
    scraped_at: str
    kind: str  # html | pdf
    sections: list[Section] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(s.words for s in self.sections)


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------
def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def visible_words(raw: bytes) -> int:
    """Words a visitor would see on the whole page, before any cleaning (the baseline)."""
    soup = BeautifulSoup(raw, "lxml")
    for el in soup(["script", "style", "noscript"]):
        el.decompose()
    return len((soup.body or soup).get_text(" ", strip=True).split())


def load_html_pages(raw_dir: Path, include: set[str]) -> tuple[list[Page], dict]:
    manifest = {r["url"]: r for r in _read_csv(raw_dir / "manifest.csv")}
    pages: list[Page] = []
    report = {"skipped": [], "empty": [], "raw_words": 0, "out_of_scope": 0}
    for rec in _read_csv(raw_dir / "urls.csv"):
        url, category, tier = rec["url"], rec["category"], int(rec["tier"])
        if tier == 2 and category not in include:
            report["out_of_scope"] += 1
            continue
        row = manifest.get(url)
        if not row or row["http_status"] != "200" or not row["path"]:
            report["skipped"].append({"url": url, "status": (row or {}).get("http_status", "")})
            continue
        raw = (raw_dir / row["path"]).read_bytes()
        cleaned = clean_html(raw, url)
        report["raw_words"] += visible_words(raw)
        if not cleaned.sections:
            report["empty"].append(url)
            continue
        pages.append(
            Page(
                slug=discover.slugify(url),
                title=cleaned.title,
                url=url,
                category=category,
                tier=tier,
                scraped_at=row["fetched_at"],
                kind="html",
                sections=cleaned.sections,
            )
        )
    return pages, report


STOPWORDS = frozenset("the of and to for in is a be by on or as with are that this at from".split())
MIN_STOPWORD_SHARE = 0.05
MIN_WORD_TOKEN_SHARE = 0.5


def looks_english(text: str) -> bool:
    """True for real English prose. Legacy-font Indic PDFs decode to Latin-looking gibberish,
    so letters alone are not enough: ordinary English has many short function words."""
    tokens = text.split()
    words = re.findall(r"[A-Za-z]+", text.lower())
    if len(words) < PDF_WORDS[0]:
        return False
    # Scanned Indic forms leave mostly symbols behind ("( ) * / *"): require real words.
    word_tokens = sum(bool(re.search(r"[A-Za-z]{2}", tok)) for tok in tokens)
    if word_tokens / len(tokens) < MIN_WORD_TOKEN_SHARE:
        return False
    return sum(w in STOPWORDS for w in words) / len(words) >= MIN_STOPWORD_SHARE


def load_pdf_pages(raw_dir: Path, html_pages: list[Page], include: set[str]) -> tuple[list, dict]:
    """Pick English, loan-specific PDFs; report why the others were left out."""
    manifest = {r["url"]: r for r in _read_csv(raw_dir / "manifest.csv")}
    page_info = {r["url"]: r for r in _read_csv(raw_dir / "urls.csv")}
    linked_from: dict[str, list[str]] = {}
    for url, row in manifest.items():
        if row["type"] != "html" or row["http_status"] != "200" or not row["path"]:
            continue
        html = (raw_dir / row["path"]).read_text(encoding="utf-8", errors="replace")
        for pdf_url in discover.extract_pdf_links(html, url):
            linked_from.setdefault(pdf_url, []).append(url)

    kept_urls = {p.url for p in html_pages}
    pages: list[Page] = []
    left_out: Counter = Counter()
    for url, row in sorted(manifest.items()):
        if row["type"] != "pdf" or row["http_status"] != "200":
            continue
        txt_path = (raw_dir / row["path"]).with_suffix(".txt")
        text = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
        parents = linked_from.get(url, [])
        words = len(text.split())
        head = " ".join(text.split()[:60]).lower()
        if not words:
            reason = "no text (scanned image)"
        elif len(parents) > PDF_MAX_LINKING_PAGES:
            reason = "site-wide footer link"
        elif not looks_english(text):
            reason = "not English"
        elif not PDF_WORDS[0] <= words <= PDF_WORDS[1]:
            reason = "too short or too long"
        elif "application form" in head:
            reason = "blank application form"
        elif not any(p in kept_urls for p in parents):
            reason = "linked only from pages outside this build"
        else:
            reason = ""
        if reason:
            left_out[reason] += 1
            continue
        parent = next(p for p in parents if p in kept_urls)
        cleaned = clean_pdf_text(pdf_to_text(raw_dir / row["path"]))
        if not cleaned.sections:
            left_out["no text (scanned image)"] += 1
            continue
        info = page_info[parent]
        pages.append(
            Page(
                slug=discover.slugify(url),
                title=cleaned.title or Path(url).stem,
                url=url,
                category=info["category"],
                tier=int(info["tier"]),
                scraped_at=row["fetched_at"],
                kind="pdf",
                sections=cleaned.sections,
            )
        )
    return pages, {"kept": len(pages), "left_out": dict(left_out)}


# --------------------------------------------------------------------------------------
# Boilerplate
# --------------------------------------------------------------------------------------
def _line_is_candidate(line: str) -> bool:
    return len(line.split()) >= MIN_LINE_WORDS and not line.startswith(("#", "|"))


def strip_boilerplate(pages: list[Page], share: float = BOILERPLATE_SHARE) -> Counter:
    """Remove text lines found on at least ``share`` of the pages; return {line: pages_with_it}."""
    html_pages = [p for p in pages if p.kind == "html"]
    if len(html_pages) < 2:
        return Counter()
    seen: Counter = Counter()
    for p in html_pages:
        lines = {ln.strip() for s in p.sections for ln in s.body.splitlines()}
        seen.update(ln for ln in lines if _line_is_candidate(ln))
    cutoff = max(2, math.ceil(share * len(html_pages)))
    boiler = {line for line, n in seen.items() if n >= cutoff}
    for p in html_pages:
        for s in p.sections:
            s.body = "\n".join(ln for ln in s.body.splitlines() if ln.strip() not in boiler).strip()
        p.sections = [s for s in p.sections if s.body]
    return Counter({line: seen[line] for line in boiler})


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------
def _front_matter(p: Page) -> str:
    meta = {
        "title": p.title,
        "url": p.url,
        "category": p.category,
        "tier": p.tier,
        "type": p.kind,
        "scraped_at": p.scraped_at,
    }
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000) + "---\n"


def page_markdown(p: Page) -> str:
    body = CleanPage(p.title, p.sections).to_markdown()
    return _front_matter(p) + "\n" + re.sub(r"\n{3,}", "\n\n", body)


IST = timezone(timedelta(hours=5, minutes=30))


def _ist_date(timestamp: str) -> str:
    """Manifest times are UTC; people reading the knowledge base think in IST."""
    return datetime.fromisoformat(timestamp).astimezone(IST).date().isoformat()


def _sort_key(p: Page) -> tuple:
    order = CATEGORY_ORDER.index(p.category) if p.category in CATEGORY_ORDER else 99
    return (order, p.kind, p.title.lower())


def write_outputs(pages: list[Page], out_dir: Path) -> None:
    pages_dir = out_dir / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True)
    ordered = sorted(pages, key=_sort_key)
    for p in ordered:
        (pages_dir / f"{p.slug}.md").write_text(page_markdown(p), encoding="utf-8")

    dates = sorted({_ist_date(p.scraped_at) for p in ordered})
    header = (
        "> Bank of Maharashtra loan information, collected from bankofmaharashtra.bank.in "
        f"(scraped {', '.join(dates)}). Rates and charges change; check the source link.\n"
    )
    blocks = [header]
    for p in ordered:
        body = CleanPage(p.title, p.sections).to_markdown()
        title_line, rest = body.split("\n", 1)
        source = f"Source: {p.url} | Category: {p.category} | Scraped: {p.scraped_at}"
        blocks.append(f"{title_line}\n\n{source}\n{rest}")
    text = "\n\n".join(blocks)
    (out_dir / "knowledge_base.md").write_text(re.sub(r"\n{3,}", "\n\n", text), encoding="utf-8")

    with (out_dir / "loan_products.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for p in ordered:
            sections: dict[str, str] = {}
            for s in p.sections:
                key = s.heading
                while key in sections:
                    key += " (2)"
                sections[key] = s.body
            record = {
                "product": p.title,
                "category": p.category,
                "url": p.url,
                "scraped_at": p.scraped_at,
                "tier": p.tier,
                "type": p.kind,
                "sections": sections,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_stats(pages, html_report, pdf_report, boiler: Counter) -> dict:
    html_pages = [p for p in pages if p.kind == "html"]
    cleaned_words = sum(p.words for p in html_pages)
    all_text = "\n".join(s.body for p in pages for s in p.sections)
    return {
        "pages_html": len(html_pages),
        "pages_pdf": len(pages) - len(html_pages),
        "by_category": dict(Counter(p.category for p in pages)),
        "sections": sum(len(p.sections) for p in pages),
        "tables": len(re.findall(r"^\s*\|[ -:|]+\|\s*$", all_text, flags=re.M)),
        "words_total": sum(p.words for p in pages),
        "raw_visible_words_html": html_report["raw_words"],
        "words_dropped_as_page_chrome": html_report["raw_words"] - cleaned_words,
        "boilerplate_lines_removed": len(boiler),
        "boilerplate_top": [[n, line[:100]] for line, n in boiler.most_common(10)],
        "thin_pages": sorted(
            f"{p.url.rsplit('/', 1)[-1]} ({p.words} words)"
            for p in html_pages
            if p.words < THIN_PAGE_WORDS
        ),
        "skipped_urls": html_report["skipped"],
        "empty_pages": html_report["empty"],
        "tier2_not_included": html_report["out_of_scope"],
        "pdfs": pdf_report,
    }


def run_process(include: set[str] | None = None) -> dict:
    include = TIER2 if include is None else include
    raw_dir, out_dir = settings.raw_dir, settings.processed_dir
    html_pages, html_report = load_html_pages(raw_dir, include)
    pdf_pages, pdf_report = load_pdf_pages(raw_dir, html_pages, include)
    pages = html_pages + pdf_pages
    boiler = strip_boilerplate(pages)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_outputs(pages, out_dir)
    stats = build_stats(pages, html_report, pdf_report, boiler)
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), "utf-8")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean scraped pages and build the knowledge base.")
    ap.add_argument(
        "--include",
        default="msme,agri",
        help="Tier 2 categories to add (default msme,agri); use 'none' for Tier 1 only",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    include = {c.strip() for c in args.include.split(",") if c.strip() and c != "none"}
    unknown = include - TIER2
    if unknown:
        ap.error(f"unknown categories {sorted(unknown)}; choose from {sorted(TIER2)}")
    stats = run_process(include)
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
