"""URL discovery: sitemap + seed list + links found on hub pages -> data/raw/urls.csv."""

import csv
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from loan_assistant.config import settings

log = logging.getLogger(__name__)

HOST = urlparse(settings.base_url).netloc
BLOCKED_PREFIXES = (
    "/blogs/",
    "/hi/",
    "/mar/",
    "/admin/",
    "/bin/",
    "/error404",
    "/tender-details",
    "/asset-for-sales-search-details",
    "/properties-for-sale-details",
)
LOAN_PREFIX = "/personal-banking/loans/"

# Hub pages (full paths) that we fetch first; their links reveal the sub-scheme URLs.
HUB_PATHS = [
    "/personal-banking/loans/home-loan",
    "/personal-banking/loans/personal-loan",
    "/personal-banking/loans/car-loan",
    "/personal-banking/loans/educational-loans",
    "/personal-banking/loans/gold-loan",
    "/personal-banking/loans/loan-against-property",
    "/retail-interest-rates",
    "/service-charges",
]

# Last path segments of Tier 1 pages named in the spec (section 2.1). Sitemap and hub links
# are matched against these, because the spec gives some of them without their parent path.
TIER1_SLUGS = {
    "home-loan",
    "maha-super-flexi-housing-loan-scheme",
    "maha-super-housing-loan-scheme-for-construction-acquiring",
    "maha-super-housing-loan-scheme-for-purchase-plot-construction-thereon",
    "maha-super-housing-loan-scheme-for-repairs",
    "topup-home-loan",
    "pradhan-mantri-awas-yojana-2",
    "personal-loan",
    "personal-loan-for-salaried-customers",
    "salary-gain-scheme",
    "car-loan",
    "mahabank-vehicle-loan-scheme-for-two-wheelers-loans",
    "mahabank-vehicle-loan-scheme-for-second-hand-car",
    "educational-loans",
    "gold-loan",
    "loan-against-property",
    "maha-adhaar-loan",
    "mahabank-green-financing-scheme",
    "mahabank-rooftop-solar-panel-loan",
    "lad",
    "retail-interest-rates",
    "service-charges",
}

LOAN_KEYWORDS = ("loan", "housing", "awas", "lad", "scheme", "interest-rate", "service-charges")
TIER2_KEYWORDS = {"msme": ("msme", "mudra", "cgtmse"), "agri": ("agri", "kisan", "farm")}
# Substrings that mark a non-product page even if the URL looks loan-related.
EXCLUDE_KEYWORDS = ("emi-calculator", "calculator", "apply-now", "digiloans", "lead")

CATEGORY_RULES = [
    ("rates", ("interest-rate",)),
    ("charges", ("service-charges",)),
    ("home", ("home", "housing", "awas", "topup")),
    ("personal", ("personal-loan", "salary", "maha-adhaar")),
    ("vehicle", ("car-loan", "vehicle")),
    ("education", ("education",)),
    ("gold", ("gold",)),
    ("property", ("property", "lad")),
]


@dataclass(frozen=True)
class UrlRecord:
    url: str
    category: str
    tier: int
    source: str  # sitemap | seed | link


def slug_of(url: str) -> str:
    """Last path segment, used for matching against TIER1_SLUGS."""
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def normalize(url: str) -> str:
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    return f"https://{p.netloc}{p.path.rstrip('/') or '/'}" + (f"?{p.query}" if p.query else "")


def is_allowed_url(url: str) -> bool:
    """Official host only (exact match), https, and not a blocked section."""
    p = urlparse(url)
    if p.scheme != "https" or p.netloc != HOST:
        return False
    if p.path.lower().endswith((".jpg", ".png", ".jpeg", ".gif", ".svg", ".zip")):
        return False
    return not any(p.path.startswith(b) for b in BLOCKED_PREFIXES)


def categorize(url: str) -> str:
    path = urlparse(url).path.lower()
    for tier2, words in TIER2_KEYWORDS.items():
        if any(w in path for w in words):
            return tier2
    for category, words in CATEGORY_RULES:
        if any(w in path for w in words):
            return category
    return "other"


def tier_of(url: str) -> int:
    if slug_of(url) in TIER1_SLUGS or urlparse(url).path.startswith(LOAN_PREFIX):
        return 1
    return 2 if categorize(url) in TIER2_KEYWORDS else 1


def looks_like_loan_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    if any(k in path for k in EXCLUDE_KEYWORDS) and slug_of(url) not in TIER1_SLUGS:
        return False
    return slug_of(url) in TIER1_SLUGS or any(k in path for k in LOAN_KEYWORDS)


def parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page urls, nested sitemap urls) from a sitemap or sitemap index."""
    root = ET.fromstring(xml_text)
    pages, nested = [], []
    for el in root.iter():
        if not el.tag.endswith("}loc") and el.tag != "loc":
            continue
        loc = (el.text or "").strip()
        if not loc:
            continue
        (nested if loc.endswith(".xml") else pages).append(loc)
    return pages, nested


def extract_links(html: str, page_url: str) -> list[str]:
    """On-domain, allowed, loan-looking links found on a page."""
    soup = BeautifulSoup(html, "lxml")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = normalize(urljoin(page_url, href))
        if is_allowed_url(url) and looks_like_loan_page(url):
            found.append(url)
    return found


def extract_pdf_links(html: str, page_url: str) -> list[str]:
    """On-domain links to PDFs (rate schedules and similar)."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"].strip())
        p = urlparse(url)
        if p.netloc == HOST and p.path.lower().endswith(".pdf"):
            out.append(urldefrag(url)[0])
    return sorted(set(out))


def build_url_list(
    sitemap_urls: list[str], extra_urls: list[tuple[str, str]] = ()
) -> list[UrlRecord]:
    """Merge seed hubs, sitemap matches and extra (url, source) pairs; dedupe; keep allowed."""
    candidates: dict[str, str] = {}
    for path in HUB_PATHS:
        candidates[normalize(settings.base_url + path)] = "seed"
    for url in sitemap_urls:
        url = normalize(url)
        if is_allowed_url(url) and looks_like_loan_page(url):
            candidates.setdefault(url, "sitemap")
    for url, source in extra_urls:
        url = normalize(url)
        if is_allowed_url(url):
            candidates.setdefault(url, source)
    records = [UrlRecord(u, categorize(u), tier_of(u), s) for u, s in sorted(candidates.items())]
    log.info("%d urls (%d tier 1)", len(records), sum(r.tier == 1 for r in records))
    return records


def missing_tier1(records: list[UrlRecord]) -> list[str]:
    """Spec Tier 1 slugs that no discovered URL matches (to report, not to hide)."""
    have = {slug_of(r.url) for r in records}
    return sorted(TIER1_SLUGS - have)


def write_urls_csv(records: list[UrlRecord], path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "category", "tier", "source"])
        for r in records:
            w.writerow([r.url, r.category, r.tier, r.source])


def read_urls_csv(path) -> list[UrlRecord]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            UrlRecord(r["url"], r["category"], int(r["tier"]), r["source"])
            for r in csv.DictReader(f)
        ]


def slugify(url: str) -> str:
    """Filesystem-safe name for a URL, e.g. 'personal-banking-loans-home-loan'."""
    path = urlparse(url).path.strip("/") or "index"
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
