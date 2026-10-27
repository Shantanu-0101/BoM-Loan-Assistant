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

## 2026-10-28 ~00:45 IST (approx.) | Phase 1 summary said 91 URLs / 51 Tier 1; the file has 90 / 50
- **Symptom:** `scrape_run2.log` ends with `urls: 91`, `tier1: 51`, but `urls.csv` has 90 rows (50 Tier 1).
- **Cause:** the log was written before the fix that stops `.pdf` files counting as pages, so it still counted `education-loan.pdf`. A leftover non-UTF-8 file from that bug (`data/raw/html/writereaddata-loans-form-education-loan-pdf.html`) is still on disk (gitignored, harmless).
- **Fix:** none needed in code. The correct numbers are 90 URLs and 50 Tier 1. The processing step reads `urls.csv` and `manifest.csv`, never the folder. Phase 2 check: 50 Tier 1 pages in the manifest, 50 pages processed.

## 2026-10-28 ~00:55 IST (approx.) | A whole page came out empty: an unclosed `heading-wrap` wrapped the article
- **Symptom:** `interest-subsidies-scheme-for-education-loans` (588 words in the raw page) had 0 words after cleaning.
- **Cause:** the site leaves `<div class="heading-wrap">` open on some pages, so it contains the entire article. The cleaner removed the wrapper to get rid of the title block, and the article went with it.
- **Fix:** only the `<h1>` (and a `<h2 class="h4">` subtitle) are taken out; the wrapper stays. Regression test: `test_unclosed_heading_wrapper_does_not_swallow_the_article`. Found by listing pages with 0 words after the first full run.

## 2026-10-28 ~01:00 IST (approx.) | FAQ questions silently lost
- **Symptom:** the cleaned home-loan page had the FAQ answers but no questions.
- **Cause:** the FAQ questions are `<button>` elements inside `<h3>`, and the cleaner dropped every `<button>` (meant for "SHOW EMI" and the like).
- **Fix:** buttons are kept as text; stand-alone button words ("Submit", "Apply Now") are filtered by a short UI-text list instead. Only the tab's own header is removed. Test: `test_faq_questions_are_kept_as_headings`, plus a check on the real page that it has at least 8 FAQ headings.

## 2026-10-28 ~01:03 IST (approx.) | Interest-rate tables flattened into unreadable text
- **Symptom:** on `retail-interest-rates`, the CIBIL-score tables (inside numbered list items) came out as `800 & above - RLLR - -1.05% - 7.25% - RLLR ...` with the columns lost. These are the core rate data.
- **Cause:** tables nested in list items were flattened to a single line.
- **Fix:** a table inside a list item is now a real Markdown table placed after its item, at column 0. Tables nested inside table cells are still flattened to one line (`label - value; label - value`), because Markdown cannot nest tables. Test: `test_table_inside_a_list_item_stays_a_real_table`.

## 2026-10-28 ~01:05 IST (approx.) | Legitimate links removed, and "know more" cut out of a sentence
- **Symptom:** a table cell read `Click here to view the` with the link text missing; a list item lost the words "know more" in the middle of a sentence.
- **Cause:** (1) a blanket rule dropped every `a.btn`, but the site also uses that class for ordinary links ("Interest Rate"). (2) The "Know More..." clean-up matched anywhere in a line, not only as a trailing link label.
- **Fix:** only the apply, call-back and calculator buttons are dropped by class name; the clean-up is anchored to the end of the text. Test: `test_know_more_is_removed_only_as_a_trailing_link_label`.

## 2026-10-28 ~01:08 IST (approx.) | A garbled PDF passed the "is it English" check
- **Symptom:** a PM SVANidhi form (Indic script, fonts that decode to Latin-looking symbols) entered the knowledge base as `opam o#T jz...`.
- **Cause:** the first check counted ASCII letters, and these glyphs are ASCII.
- **Fix:** a PDF now needs real English prose: at least 50% of its tokens must contain a real word, and at least 5% of its words must be common function words ("the", "of", "and"). 18 non-English or garbled PDFs are left out; 14 scanned ones have no text. Test: `test_pdf_language_check_accepts_english_and_rejects_font_gibberish`.

Note: the timestamps above before 00:28 are approximate. The network and sitemap entries were first written with a wrong clock reading, so only the date and the order are reliable for those two.
