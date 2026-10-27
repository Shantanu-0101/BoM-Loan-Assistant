from loan_assistant.scraping import discover

BASE = "https://bankofmaharashtra.bank.in"

SITEMAP = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{BASE}/personal-banking/loans/home-loan/maha-super-flexi-housing-loan-scheme</loc></url>
  <url><loc>{BASE}/personal-banking/loans/personal-loan/salary-gain-scheme</loc></url>
  <url><loc>{BASE}/blogs/best-home-loan-tips</loc></url>
  <url><loc>{BASE}/hi/personal-banking/loans/home-loan</loc></url>
  <url><loc>https://digiloans.bankofmaharashtra.bank.in/home-loan</loc></url>
  <url><loc>https://bankofmaharashtra.in/home-loan</loc></url>
  <url><loc>{BASE}/about-us</loc></url>
  <url><loc>{BASE}/personal-banking/loans/home-loan/emi-calculator</loc></url>
</urlset>"""


def test_parse_sitemap_returns_pages_and_nested():
    index = '<sitemapindex xmlns="x"><sitemap><loc>https://a/b.xml</loc></sitemap></sitemapindex>'
    assert discover.parse_sitemap(index) == ([], ["https://a/b.xml"])
    pages, nested = discover.parse_sitemap(SITEMAP)
    assert len(pages) == 8 and nested == []


def test_only_official_host_and_allowed_sections():
    assert discover.is_allowed_url(f"{BASE}/personal-banking/loans/home-loan")
    assert not discover.is_allowed_url(f"{BASE}/blogs/x")
    assert not discover.is_allowed_url(f"{BASE}/hi/x")
    assert not discover.is_allowed_url(f"{BASE}/mar/x")
    assert not discover.is_allowed_url("https://digiloans.bankofmaharashtra.bank.in/x")
    assert not discover.is_allowed_url("https://bankofmaharashtra.in/x")
    assert not discover.is_allowed_url("http://bankofmaharashtra.bank.in/x")


def test_build_url_list_filters_and_keeps_hubs():
    pages, _ = discover.parse_sitemap(SITEMAP)
    records = discover.build_url_list(pages)
    urls = {r.url for r in records}
    assert f"{BASE}/personal-banking/loans/home-loan" in urls  # hub seed
    assert f"{BASE}/personal-banking/loans/home-loan/maha-super-flexi-housing-loan-scheme" in urls
    assert f"{BASE}/personal-banking/loans/personal-loan/salary-gain-scheme" in urls
    assert not any("/blogs/" in u or "/hi/" in u or "digiloans" in u for u in urls)
    assert not any(u.endswith("/about-us") or "emi-calculator" in u for u in urls)
    assert all(discover.urlparse(u).netloc == "bankofmaharashtra.bank.in" for u in urls)


def test_all_hubs_are_tier1_and_categorized():
    records = {r.url: r for r in discover.build_url_list([])}
    assert records[f"{BASE}/retail-interest-rates"].category == "rates"
    assert records[f"{BASE}/service-charges"].category == "charges"
    assert records[f"{BASE}/personal-banking/loans/home-loan"].category == "home"
    assert all(r.tier == 1 for r in records.values())


def test_extract_links_keeps_only_loan_links_on_domain():
    html = f"""<a href="/personal-banking/loans/home-loan/topup-home-loan">t</a>
    <a href="{BASE}/blogs/x-loan">b</a><a href="/about-us">a</a>
    <a href="https://example.com/home-loan">e</a>
    <a href="/personal-banking/loans/home-loan/topup-home-loan#faq">dup</a>"""
    links = discover.extract_links(html, BASE + "/personal-banking/loans/home-loan")
    assert set(links) == {f"{BASE}/personal-banking/loans/home-loan/topup-home-loan"}


def test_extract_pdf_links_same_domain_only():
    html = '<a href="/docs/rates.pdf">r</a><a href="https://other.com/x.pdf">o</a>'
    assert discover.extract_pdf_links(html, BASE + "/x") == [f"{BASE}/docs/rates.pdf"]


def test_missing_tier1_reports_unfound_slugs():
    records = discover.build_url_list([])
    assert "lad" in discover.missing_tier1(records)
    assert "home-loan" not in discover.missing_tier1(records)


def test_csv_roundtrip(tmp_path):
    records = discover.build_url_list([])
    path = tmp_path / "urls.csv"
    discover.write_urls_csv(records, path)
    assert discover.read_urls_csv(path) == records
