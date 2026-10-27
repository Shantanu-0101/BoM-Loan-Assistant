import csv
import json
import shutil
from pathlib import Path

import yaml

from loan_assistant.processing.clean import Section
from loan_assistant.processing.consolidate import (
    Page,
    load_html_pages,
    looks_english,
    strip_boilerplate,
    write_outputs,
)

FIXTURES = Path(__file__).parent / "fixtures"
SITE = "https://bankofmaharashtra.bank.in"
NOTICE = "Rates are linked to the repo rate and may change without notice."


def page(n: int, *lines: str) -> Page:
    body = "\n".join(lines)
    return Page(
        f"p{n}",
        f"Page {n}",
        f"{SITE}/p{n}",
        "home",
        1,
        "2026-10-27T18:00:00+00:00",
        "html",
        [Section("Overview", body)],
    )


def test_boilerplate_found_on_half_the_pages_is_removed_and_reported():
    pages = [page(i, NOTICE, f"Unique fact number {i} about this scheme.") for i in range(1, 5)]
    pages += [page(5, "Only here.")] * 2
    removed = strip_boilerplate(pages)
    assert removed == {NOTICE: 4}
    assert all(NOTICE not in s.body for p in pages for s in p.sections)
    assert "Unique fact number 1 about this scheme." in pages[0].sections[0].body


def test_short_lines_headings_and_tables_are_never_boilerplate():
    keep = ["- Yes", "### Eligibility", "| a | b | c | d | e |"]
    pages = [page(i, *keep) for i in range(1, 5)]
    assert strip_boilerplate(pages) == {}
    assert pages[0].sections[0].body.splitlines() == keep


def test_pdf_language_check_accepts_english_and_rejects_font_gibberish():
    english = "The rate of interest for the loan is fixed and the borrower must pay it. " * 10
    gibberish = "opam o#T jz³drs E±¿u onzur ono] k¶¿n jlun aopBjksn " * 20
    assert looks_english(english)
    assert not looks_english(gibberish)
    assert not looks_english("too short")


def make_raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / "html").mkdir(parents=True)
    shutil.copy(FIXTURES / "sample_tabbed_page.html", raw / "html" / "sample.html")
    shutil.copy(FIXTURES / "sample_article_page.html", raw / "html" / "flexi.html")
    shutil.copy(FIXTURES / "sample_article_page.html", raw / "html" / "agri.html")
    urls = [("sample", "home", 1), ("flexi", "home", 1), ("agri", "agri", 2), ("moved", "msme", 2)]
    with (raw / "urls.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "category", "tier", "source"])
        w.writerows([(f"{SITE}/{s}", c, t, "seed") for s, c, t in urls])
    with (raw / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "type", "http_status", "fetched_at", "bytes", "sha256", "path"])
        for s in ("sample", "flexi", "agri"):
            w.writerow(
                [f"{SITE}/{s}", "html", 200, "2026-10-27T18:00:00+00:00", 1, "x", f"html/{s}.html"]
            )
        w.writerow([f"{SITE}/moved", "html", 301, "2026-10-27T18:00:00+00:00", 0, "", ""])
    return raw


def test_tier2_is_left_out_unless_asked_for(tmp_path):
    raw = make_raw(tmp_path)
    pages, report = load_html_pages(raw, include=set())
    assert sorted(p.slug for p in pages) == ["flexi", "sample"]
    assert report["out_of_scope"] == 2 and report["skipped"] == []

    pages, report = load_html_pages(raw, include={"agri", "msme"})
    assert sorted(p.slug for p in pages) == ["agri", "flexi", "sample"]
    assert report["skipped"] == [{"url": f"{SITE}/moved", "status": "301"}]  # off-site redirect


def test_outputs_are_consistent_with_each_other(tmp_path):
    raw = make_raw(tmp_path)
    pages, _ = load_html_pages(raw, include=set())
    out = tmp_path / "processed"
    write_outputs(pages, out)

    files = sorted((out / "pages").glob("*.md"))
    assert [f.stem for f in files] == ["flexi", "sample"]
    front = yaml.safe_load(files[1].read_text(encoding="utf-8").split("---")[1])
    assert front["title"] == "Sample Housing Loan Scheme"
    assert front["url"] == f"{SITE}/sample" and front["category"] == "home"
    assert front["scraped_at"] == "2026-10-27T18:00:00+00:00"

    records = [
        json.loads(line) for line in (out / "loan_products.jsonl").read_text("utf-8").splitlines()
    ]
    assert len(records) == len(files) == 2
    sample = next(r for r in records if r["url"].endswith("/sample"))
    assert list(sample["sections"]) == ["Overview", "Features & Benefits", "At a Glance", "FAQs"]

    kb = (out / "knowledge_base.md").read_text(encoding="utf-8")
    assert kb.count("\n# ") + kb.startswith("# ") == 2  # one top-level section per product
    assert f"Source: {SITE}/sample" in kb and f"Source: {SITE}/flexi" in kb
