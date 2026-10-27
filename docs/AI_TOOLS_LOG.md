# AI tools log

One row per tool and phase. The owner checks and completes this before submission.

| Tool | Phase | Used for | Example prompt | What it produced | Kept or changed |
|---|---|---|---|---|---|
| Claude Code (Sonnet 5.5) | 2: cleaning and knowledge base | Wrote `processing/clean.py`, `pdf_text.py`, `consolidate.py` and their tests; read the saved pages to find cleaning bugs | "Start Phase 2 (cleaning and knowledge base) now." | Section-aware HTML to Markdown cleaner (tabbed and article layouts), table conversion, PDF filter, `knowledge_base.md`, `loan_products.jsonl`, 50 tests | Output checked page by page; 6 bugs found and fixed in review (see `docs/ERROR_LOG.md`). Phases 0 and 1 rows: owner to add. |
