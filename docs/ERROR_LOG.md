# Error and challenge log

Real problems met while building, in time order (IST). The README's "Challenges Faced" section draws from this.

## 2026-10-27 night (time not recorded) | Site unreachable from the development laptop
- **Symptom:** every request to `bankofmaharashtra.bank.in` failed (`NewConnectionError`; `curl` could not connect). Google worked.
- **Cause:** the Wi-Fi network gave the laptop only an IPv6 address (IPv4 was link-local `169.254.x.x`). The site's IPv4 address was unreachable, and its IPv6 address timed out. This is risk T1 in the spec.
- **Fix:** switched to a network with working IPv4. Diagnosed with raw socket connects to each address, `ipconfig`, and `curl -4` / `curl -6`.
- **Lesson:** a failing scrape can be the network, not the code. If it recurs, check IPv4 first.

## 2026-10-28 ~00:20 IST (approx.) | `sitemap.xml` failed to parse
- **Symptom:** `ParseError: not well-formed (invalid token): line 1, column 1`.
- **Cause:** the live sitemap starts with a UTF-8 byte-order mark (BOM).
- **Fix:** `parse_sitemap` strips the BOM first. Regression test: `test_parse_sitemap_tolerates_utf8_bom`.

## 2026-10-28 ~00:25 IST (approx.) | Page paths in the spec did not match the real site
- **Symptom:** the spec's Tier 1 slugs were partly given without their parent path, and my guessed hub paths such as `/personal-banking/loans/gold-loan` did not exist.
- **Cause:** only home, personal and car loans live under `/personal-banking/loans/`; every other loan page is at the site root with a flat slug.
- **Fix:** read the real sitemap (1,333 URLs), matched by slug, and replaced the keyword filter with a word-based classifier (`looks_like_loan_page`) that includes loan words and excludes deposits, calculators, accounts, corporate pages and partner-fintech pages. A test covers both lists.

## 2026-10-28 ~00:28 IST | `/msme` and `/agriculture` redirect to the retired domain
- **Symptom:** the first live scrape failed for two pages with `NameResolutionError` for `www.bankofmaharashtra.in`.
- **Cause:** the official site still redirects these two URLs to the old `bankofmaharashtra.in` domain, which no longer resolves. The pages they point to (`/agricultures`, `/msme_large_credit`) are fetched under their own URLs.
- **Fix:** the fetcher follows redirects only on the official host. An off-site redirect is recorded in the manifest as 301 and not retried. Tests cover both cases. This also keeps the "official domain only" rule true for redirects.

## 2026-10-28 ~00:30 IST | PDF file counted as a page; scanned PDFs have no text
- **Symptom:** `education-loan.pdf` appeared in `urls.csv`; 15 of the 45 downloaded PDFs gave no text from PyMuPDF.
- **Cause:** the loan-word filter matched the filename. The empty PDFs are most likely scans (images only).
- **Fix:** `.pdf` and `.aspx` paths are never treated as pages. Scanned PDFs are left out of the knowledge base; OCR is listed as a possible improvement. The 70 MB of PDF files are gitignored; only extracted text and the manifest are kept.

## 2026-10-28 ~00:25 IST | `python -m loan_assistant...` failed with ModuleNotFoundError
- **Cause:** the package was not installed in the venv.
- **Fix:** `pip install -e .` (now a documented setup step).

Note: the timestamps above before 00:28 are approximate. The network and sitemap entries were first written with a wrong clock reading, so only the date and the order are reliable for those two.
