# Error and challenge log

Real problems met while building, in time order (IST). The README's "Challenges Faced" section draws from this.

## 2026-10-27 night (time not recorded) | Site unreachable from the development laptop
- **Symptom:** every request to `bankofmaharashtra.bank.in` failed (`NewConnectionError`; `curl` could not connect). Google worked.
- **Cause:** the Wi-Fi network gave the laptop only an IPv6 address (IPv4 was link-local `169.254.x.x`). The site's IPv4 address was unreachable, and its IPv6 address timed out.
- **Fix:** switched to a network with working IPv4. Diagnosed with raw socket connects to each address, `ipconfig`, and `curl -4` / `curl -6`.
- **Lesson:** a failing scrape can be the network, not the code. If it recurs, check IPv4 first.

## 2026-10-28 ~00:20 IST (approx.) | `sitemap.xml` failed to parse
- **Symptom:** `ParseError: not well-formed (invalid token): line 1, column 1`.
- **Cause:** the live sitemap starts with a UTF-8 byte-order mark (BOM).
- **Fix:** `parse_sitemap` strips the BOM first. Regression test: `test_parse_sitemap_tolerates_utf8_bom`.

## 2026-10-28 ~00:25 IST (approx.) | Page paths I was given did not match the real site
- **Symptom:** the Tier 1 slugs were partly given without their parent path, and my guessed hub paths such as `/personal-banking/loans/gold-loan` did not exist.
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
- **Left as:** a documented limitation. Candidate fixes for the README's improvements list: a cross-encoder reranker, or a structured rate table with direct lookup. Phase 4 will measure how many rate questions are affected.

## 2026-10-28 ~01:25 IST | Giant table cells are cut mid-row
- **Symptom:** in the chunks of `/retail-interest-rates`, two personal-loan chunks are exactly 450 words, and the next one starts in the middle of a row (`RLLR - 2.95% - 11.25%; ...`).
- **Cause:** Phase 2 flattens nested tables into one table cell, and that one row is longer than the 450-word limit. The chunker never splits a row, except when a single row alone exceeds the limit; then it cuts that row (3 cases in 415 chunks) and the header is lost.
- **Fix:** none; kept the hard limit. Listed as a limitation.

## 2026-10-28 ~02:05 IST | A hand-written key fact was not on the page it came from
- **Symptom:** the new test `test_every_key_fact_is_written_on_an_expected_page` failed twice: "10 crore" (loan against property) and "7.5 lakh" (skill loan) were not found on their pages.
- **Cause:** I wrote two key facts the way a person would say them. The LAP page prints the ceiling as `10.00` under a "Rs. in crore" caption, and the skill-loan page prints `Rs. 7,50,000`. The facts came from my reading, not from the exact text.
- **Fix:** the key facts now accept the page's own form (`10 crore | 10.00`, `7.5 lakh | 750000`). The test stays, so every fact in `eval/questions.jsonl` is proven to be on its official page.
- **Lesson:** a test that checks the answer key against the source is cheap and caught two errors before any grading.

## 2026-10-28 ~02:10 IST | The first full evaluation run lost answers to Groq's token limit
- **Symptom:** several questions failed with `RateLimitError`; the Gemini fallback was down at the same moment (503 / 504), so the run had `all providers failed` errors.
- **Cause:** the free Groq tier allows 8,000 tokens a minute for `gpt-oss-120b`. One answer costs about 2,500 tokens (5 chunks plus reasoning), so only about 3 answers fit in a minute. The first pause between calls was 2 s (based on a 30 requests/minute limit, which is not the binding limit).
- **Fix:** the evaluation waits 15 s between live calls and, on a rate-limit error, waits 20 s and retries up to 4 times. Failed calls are never cached. Successful answers are cached in `eval/cache/`, so a re-run costs no API calls.
- **Lesson:** token limits, not request limits, bound this pipeline on the free tier. The same limit will affect a reviewer who asks several questions quickly in the UI.

## 2026-10-28 ~02:50 IST | Gemini judge could not run: free quota exhausted
- **Symptom:** every judge call returned `429 RESOURCE_EXHAUSTED` (and earlier 503 / 504 on the fallback). Zero verdicts were produced, so the judge column in the reports is empty.
- **Cause:** the Gemini free-tier quota was used up (the Phase 3 check and the first evaluation attempts both spent it). Not a code fault.
- **Fix / status:** the judge is implemented and tested (`Judge`, cached verdicts, retries) but has not produced any numbers. Run `python -m loan_assistant.evaluate --mode both` again after the quota resets. Until then the README must not claim a judge-vs-hand agreement rate. Time wasted: about 25 minutes of a run that kept retrying; fixed by using `--no-judge` (answers are cached).

## 2026-10-28 ~02:40 IST | Citations written as `[1†L4-L5]` showed no Sources
- **Symptom:** for q07, q15, q17 and q18 the answer text was right but the Sources list was empty (the UI would show no links).
- **Cause:** the model sometimes adds a line reference after the number, like `[1†L4-L5]`. The citation parser only knows `[1]`.
- **Fix:** `clean_model_text` rewrites `[n†...]` to `[n]`. Test: `test_line_references_after_a_citation_number_are_dropped`. The cached answers keep the raw text; the fix applies when they are read, so no new API calls were needed.

## 2026-10-28 ~02:45 IST | Evaluation findings (hand-read; first grades by Claude, then read and confirmed by Shantanu)
- **Invented number survives the prompt rule (hybrid q01):** the home-loan rate answer says "Women borrowers: 7.20% p.a. (7.25% - 0.05%)". Rule 3 forbids arithmetic, and the model did it anyway in this run. The vector run did not. Graded 1, not 2. This is a limit of prompt-only control.
- **Rate range still missing (q01, both modes):** neither mode gives the CIBIL-based range (7.25% to 9.40%). Known weakness from Phase 3, now measured.
- **Vector wrongly refused q13 (loan against FD):** the `/lad` page was not in the vector top 5, so the model said "not found". Hybrid found it at rank 4. The one clear case where keyword search helps.
- **The bank's own gold-loan page gives two rates:** 8.75% in the headline and "starts from 8.35%" lower down. The answer reports both, which is faithful. Not a bug in our system.
- **One answer key was too strict:** q02 lists "60 months" as a key fact, but the question only needs 84 months. The automatic check scores it 50% in hybrid while the hand grade is 2. I left the key unchanged so the key is not tuned after seeing answers.
- **Refusal worked through two layers:** the other-bank rule caught "SBI" before retrieval; the weather and savings-account questions were stopped by the 0.68 cosine threshold (0.497 and 0.605). The savings-account question is the closest call.

Note: the timestamps above before 00:28 are approximate. The network and sitemap entries were first written with a wrong clock reading, so only the date and the order are reliable for those two.

## 2026-10-28 ~03:30 IST (approx.) | Fresh clone failed on Windows: "Filename too long"
- **Symptom:** `git clone` into a deep folder (about 140 characters of parent path) ended with `unable to create file ... Filename too long` and `unable to checkout working tree`.
- **Cause:** three tracked files (the PDF text and Markdown for MUDRA FAQs and the staff-pensioner life certificate) have names up to 119 characters, taken from the bank's PDF URLs. Windows allows 260 characters for the whole path. My first test folder was inside a long temporary path.
- **Fix / status:** not renamed (the names are the processing step's keys, and renaming means rebuilding). The same clone from `C:\fc_test` worked: `pip install -r requirements.txt`, `pytest` (118 passed, 2 skipped because raw HTML and PDFs are not committed), `ruff check`, `mypy` and a live question all passed. The README setup tells Windows users to clone into a short path or run `git config --global core.longpaths true`.
- **Lesson:** a fresh-clone test in a realistic location is worth doing; an OneDrive Desktop path is already long.

## 2026-10-28 ~04:00 IST (approx.) | Gemini judge and fallback re-checked after the quota reset
- **Symptom:** the first test call to `gemini-flash-latest` returned 503 ("high demand"), the next ones 429.
- **Cause:** `gemini-flash-latest` currently points to `gemini-3.8-flash`, whose free tier allows only 20 requests a day per model. The quota had not been reset for that model (retry hint: about 4 hours). `gemini-2.5-flash` and `gemini-2.0-flash` give 404 ("no longer available to new users"). Quotas are per model.
- **Fix / status:** `gemini-3.5-flash-lite`, `gemini-3.5-flash` and `gemini-3.1-flash-lite` answer on this key. With `GEMINI_MODEL=gemini-3.5-flash-lite` set only in the shell, the fallback test passed (Groq key rejected, so Gemini answered a real question with sources) and the judge produced 25 verdicts per mode. `config.py` still names `gemini-flash-latest`. A moving alias is a risk for a reproducible evaluation; consider pinning a model.
- **Result:** judge grade 2: vector 19/25, hybrid 20/25; agrees with the hand grades on 22/25 and 21/25. The disagreements (q02, q05, q08, q14) come from key facts the question did not ask for, or from the bank's own double rate on the gold-loan page.

## 2026-10-28 ~05:00 IST (approx.) | Number check added after the invented 7.20%; three false alarms found and fixed
- **Problem it fixes:** the hybrid answer to q01 wrote "7.20%" for women (7.25% minus 0.05%) although prompt rule 3 forbids arithmetic.
- **Fix:** `rag/grounding.py` compares every figure in the answer with the figures in the five retrieved chunks and in the question. A sentence with an unsupported figure is removed (only the bracketed aside if the rest is supported), a note says a figure was left out, and an answer left with no cited claim becomes the standard "not found" text. 13 tests.
- **First version, three false alarms (seen when I diffed the 50 cached answers):** (1) "0 %" for the bank's word "zero"; (2) the bank's date `31.12.2025` was read as the decimal 31.12, so the model's `31-12-2025` looked invented; (3) one unsupported "(15 years)" conversion took the whole correct line "up to 180 months" with it. Fixed: 0 is not checked; `dd.mm.yyyy` dates are split into numbers; a bracketed aside is removed on its own.
- **Result:** over 50 answers only two changed: hybrid q01 (7.20% removed) and hybrid q12 ("(15 years)" removed). No score moved (hit@5, key facts, judge, refusals identical).
- **Known gaps:** the check sees numbers, not words ("does not vary with tenure" still passes); whole numbers below 10 without a unit are not checked; "10 lakh" against "10,00,000" would be flagged. The missing CIBIL range for q01 is retrieval, not generation, and is unchanged.
