"""Download on-domain PDFs linked from loan pages and extract their text."""

import logging

import pymupdf

from loan_assistant.scraping import discover

log = logging.getLogger(__name__)


def extract_pdf_text(data: bytes) -> str:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return "\n\n".join(page.get_text().strip() for page in doc).strip()


def fetch_linked_pdfs(fetcher, page_urls: list[str]) -> dict:
    """Find PDF links in cached pages, fetch them, and write <slug>.txt next to each PDF."""
    pdf_urls: set[str] = set()
    for url in page_urls:
        row = fetcher.manifest.get(url)
        path = fetcher.raw_dir / row["path"] if row and row["path"] else None
        if path and path.exists():
            html = path.read_text(encoding="utf-8", errors="replace")
            pdf_urls.update(discover.extract_pdf_links(html, url))

    extracted = 0
    for url in sorted(pdf_urls):
        res = fetcher.get(url, kind="pdf")
        if res.status != 200:
            continue
        text = extract_pdf_text(res.content)
        out = fetcher.cache_path(url, "pdf").with_suffix(".txt")
        out.write_text(text, encoding="utf-8")
        extracted += 1
    log.info("pdfs found=%d extracted=%d", len(pdf_urls), extracted)
    return {"found": len(pdf_urls), "extracted": extracted}
