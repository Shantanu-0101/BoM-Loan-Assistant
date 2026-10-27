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

## 2026-10-28 ~01:35 IST | The type checker found 32 errors in Phases 0 to 2
- **Symptom:** `mypy` (added this phase; only `ruff` and `pytest` had been run before) reported 32 errors in `clean.py`, `discover.py`, `fetch.py` and `consolidate.py`. All earlier checks were green, so none had been seen.
- **Cause:** (1) BeautifulSoup returns `PageElement`, which the cleaner passed to functions that need `Tag`; (2) empty containers without a type (`seen = set()`); (3) a `()` default for a `list` parameter; (4) a `dict` report that mixed ints and lists.
- **Fix:** narrowing with `isinstance(child, Tag)`, a `TypeGuard` on `_is_tag`, `select_one("h1")` in place of `find("h1")`, explicit annotations, `Sequence` for the parameter, `dict[str, Any]` for the report. Behaviour is unchanged: re-running `consolidate` rebuilt the same `data/processed/` files (no diff), and all 50 earlier tests still pass. `mypy` is now configured in `pyproject.toml` and pinned in `requirements.txt`.

## 2026-10-28 ~01:40 IST | Gemini hello-world took 407 seconds
- **Symptom:** the Phase 3 check script ran for nearly 7 minutes. Groq answered in 0.9 s; Gemini answered "OK" after 407 s.
- **Cause:** not diagnosed; the default client waits and retries without a short limit. A fallback that can hang for minutes is worse than none.
- **Fix:** both providers get a 30 s timeout, and Gemini is limited to one attempt (`HttpRetryOptions(attempts=1)`). If it recurs, Gemini is the slow part, not the code. Gemini has not been used for a real answer yet (Groq has always answered).

## 2026-10-28 ~01:45 IST | Answers had no sources: the model wrote citations as 【2】
- **Symptom:** the first full answer was good, but the "Sources" list was empty.
- **Cause:** `gpt-oss-120b` writes citations in full-width brackets (`【2】`), not `[2]`, and uses non-breaking hyphens and spaces. The citation parser looked for `[2]`.
- **Fix:** the prompt now asks for plain brackets, and `clean_model_text` converts the variants to ASCII before the answer is shown or parsed. Test: `test_model_bracket_variants_are_turned_into_plain_citations`.

## 2026-10-28 ~01:48 IST | The model invented a number by subtracting
- **Symptom:** for the home-loan rate question the answer said "0.05% concession (i.e., 7.20% p.a.)". 7.20% is in no source; it is 7.25% minus 0.05%. The retail rate page says the concession applies only to some CIBIL slabs, so the number may also be wrong.
- **Cause:** the prompt said "never guess numbers" but did not forbid arithmetic.
- **Fix:** rule 3 now says: do no arithmetic, use only figures written in the context. The invented figure is gone. A second unsupported remark ("the rate does not vary with loan tenure") still appeared in one run, so the model is not fully faithful. Phase 4 hand grading must check for this.

## 2026-10-28 ~01:50 IST | Home-loan rate question gets a correct but thin answer (known weakness, not fixed)
- **Symptom:** "What are the interest rates for a Bank of Maharashtra home loan?" is answered with 7.25% p.a. and the women's concession, but not the CIBIL-score rate table on `/retail-interest-rates`, which has the full range.
- **Cause:** that table chunk ranks 28th by meaning and 122nd by keywords. Rate chunks are mostly numbers, so they embed poorly, and the page says "Housing Loan" where people say "home loan". Large "Personal Loan" tables outrank it for any "loan interest rate" question.
- **Tried, no gain (reverted or not kept):** the BGE query-instruction prefix; query synonyms home/housing and car/vehicle for BM25 (helped one question, hurt another); starting a new chunk at each numbered scheme; picking the best "rates" chunk by meaning (it picks the wrong loan).
- **Left as:** a documented limitation. Candidate fixes for the README's improvements list: a cross-encoder reranker, or a structured rate table with direct lookup (both named in the spec). Phase 4 will measure how many rate questions are affected.

## 2026-10-28 ~01:25 IST | Giant table cells are cut mid-row
- **Symptom:** in the chunks of `/retail-interest-rates`, two personal-loan chunks are exactly 450 words, and the next one starts in the middle of a row (`RLLR - 2.95% - 11.25%; ...`).
- **Cause:** Phase 2 flattens nested tables into one table cell, and that one row is longer than the 450-word limit. The chunker never splits a row, except when a single row alone exceeds the limit; then it cuts that row (3 cases in 415 chunks) and the header is lost.
- **Fix:** none; kept the hard limit. Listed as a limitation.

Note: the timestamps above before 00:28 are approximate. The network and sitemap entries were first written with a wrong clock reading, so only the date and the order are reliable for those two.
