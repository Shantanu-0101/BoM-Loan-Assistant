# Implementation plan: BoM Loan Product Assistant (RAG)

Source: `BOM_LOAN_ASSISTANT_SPEC.md` v1.0 and `Technical_Assessment_PaiKane.pdf` (the PDF wins on conflicts).
Deadline: 8pm 29/10/2026 IST. Submit by H+40 at the latest.

## Environment notes
- Machine has Python 3.13 and 3.14, not 3.12 (the spec prefers 3.12). Use a 3.13 venv. Check that `fastembed` and `onnxruntime` install in Phase 0. If they do not, install Python 3.12.
- `faiss-cpu`, `pymupdf`, `beautifulsoup4`, `pydantic-settings` and `google-genai` are already installed globally. `fastembed`, `rank_bm25`, `groq` and `streamlit` are not.
- If `faiss-cpu` fails in the venv, use the NumPy store (spec FR-C3).

## Phase 0: Setup (H+0 to H+1)
- [ ] `git init`; create the layout from spec 4.3; `.gitignore` (`.env`, `.venv`, `data/raw/html/`, `__pycache__`); copy the PDF to `docs/`
- [ ] `.venv` (3.13), pinned `requirements.txt`, `.env.example`, `pyproject.toml` (ruff + pytest config)
- [ ] `config.py` with pydantic-settings (paths, model names, top_k=5, thresholds)
- [ ] Draft the clarification email (spec 2.4, Q2/4/5) in `docs/`. The owner sends it.
- **Check:** `python -c "import faiss, fastembed"` works; Groq and Gemini hello-world calls work

## Phase 1: Scrape (H+1 to H+5)
- [ ] `scraping/discover.py`: sitemap + Tier 1 seeds -> `data/raw/urls.csv`. Host is exactly `bankofmaharashtra.bank.in`; no `/blogs/`, `/hi/`, `/mar/`, no sub-domains. Test: `test_discover.py`
- [ ] `scraping/fetch.py`: robots.txt, descriptive User-Agent, >= 1 s delay, 3 retries with backoff, 20 s timeout, HTML cache, `manifest.csv` (url, status, fetched_at, bytes, sha256), `--refresh`
- [ ] `scraping/pdf.py`: linked on-domain PDFs via PyMuPDF; manifest type=pdf (or the README says none were found)
- [ ] Playwright fallback only for pages under 200 words after cleaning
- **Checkpoint:** all Tier 1 pages in `data/raw/`, manifest all 200, second run makes zero requests

## Phase 2: Process (H+5 to H+8)
- [ ] `processing/clean.py`: use `#pane-*` sections, fall back to the main container; drop nav, footer, scripts and widgets; keep tables as Markdown; normalize whitespace, Rs./INR and percents. Test: `test_clean.py` with a saved sample page
- [ ] `data/processed/pages/<slug>.md` with YAML front-matter (title, url, category, scraped_at)
- [ ] `processing/consolidate.py`: `knowledge_base.md`, `loan_products.jsonl`; remove boilerplate found on >= 50% of pages; print stats
- **Checkpoint:** read home, personal-salaried and flexi pages by eye (no junk, tables intact); product count matches the manifest

## Phase 3: RAG core (H+8 to H+12)
- [ ] `processing/chunk.py`: by section, ~300 words (max 450), 50 overlap, never split a table mid-row, repeat the header on row groups, context header on each chunk. Test: `test_chunk.py`. Output: `chunks.jsonl`
- [ ] `rag/embed.py` (FastEmbed `BAAI/bge-small-en-v1.5`) and `rag/index.py` (FAISS `IndexFlatIP` + `chunks_meta.json`)
- [ ] `rag/retrieve.py`: FAISS top-20 + BM25 top-20, RRF (k=60), top 5; `retrieval_mode: vector|hybrid`. Test: `test_retrieve.py` (correct page in top 5 for the 4 PDF questions)
- [ ] `rag/prompt.py` (spec 5.4), `llm/providers.py` (Groq with Gemini fallback), `rag/generate.py`, `rag/pipeline.py` (guardrail: off-topic or low score -> polite refusal; disclaimer with scraped_at). Test: `test_pipeline.py` with a mocked LLM
- [ ] `ask.py` (`--show-context`) and `build.py` (`--all | --step`)
- **Checkpoint:** all 4 PDF questions answered correctly in the CLI with `[n]` source links

## Phase 4: UI and evaluation (H+20 to H+26)
- [ ] `app/streamlit_app.py`: chat, Sources expander, retrieved-chunks toggle, "Data as of" footer, 4 example buttons, provider shown
- [ ] `eval/questions.jsonl`: 25 questions (4 PDF + 18 product + 3 out-of-scope). Key facts are written by hand from the official pages; the owner verifies them
- [ ] `evaluate.py --mode vector|hybrid`: hit@5, hand grades (`grades.csv`), Gemini judge and agreement rate, refusal accuracy, latency -> `eval/results/<date>_<mode>.md`. Cache LLM answers
- **Checkpoint:** results table generated; >= 80% correct, or the reason documented

## Phase 5: Quality and docs (H+26 to H+32)
- [ ] `ruff check` and `ruff format` clean, type hints, logging, `pytest` green
- [ ] `README.md` per spec 6.2 (11 sections, vector vs hybrid table, honest failures). The reasoning sections are written or edited by the owner
- [ ] `docs/AI_TOOLS_LOG.md` (one row per tool)
- [ ] Fresh-clone test in a new folder
- **Feature freeze at H+32.** Only fixes and docs after this.

## Phase 6: Video and submit (H+32 to H+36)
- [ ] Video script outline (spec 6.3), prepared in advance
- [ ] Owner records (<= 5:00), uploads, checks both links in a private window
- [ ] Owner sends the submission email

## If time runs short
Cut from the bottom: Tier 2 data, chunk-size comparison, LLM judge, Streamlit polish, Gemini fallback.
Never cut: the 4 PDF questions working, the README, processed data in the repo, the video.

## Working rules
- One fresh chat per phase. Each phase ends with a short summary, a commit and a line in `docs/AI_TOOLS_LOG.md`.
- Official domain only. Keys stay in `.env` and are never committed or echoed.
- Do not paste raw HTML or long logs into chat; scripts print short summaries.
- No emails sent, repo published or video uploaded without the owner's go-ahead.

## End-to-end verification
`pip install -r requirements.txt` -> set `GROQ_API_KEY` -> `python -m loan_assistant.build --all` -> `python -m loan_assistant.ask "<each PDF question>"` -> `streamlit run app/streamlit_app.py` -> `pytest` -> `ruff check` -> `python -m loan_assistant.evaluate --mode hybrid`
