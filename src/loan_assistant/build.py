"""One-command rebuild: ``python -m loan_assistant.build --all`` or ``--step <name>``.

Steps, in order: scrape (uses the cache unless --refresh), process, chunk, index.
"""

import argparse
import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

STEPS = ("scrape", "process", "chunk", "index")


def _scrape(refresh: bool) -> dict[str, Any]:
    from loan_assistant.scraping.fetch import run_scrape

    return run_scrape(refresh=refresh)


def _process(refresh: bool) -> dict[str, Any]:
    from loan_assistant.processing.consolidate import run_process

    return run_process()


def _chunk(refresh: bool) -> dict[str, Any]:
    from loan_assistant.processing.chunk import run_chunk

    return run_chunk()


def _index(refresh: bool) -> dict[str, Any]:
    from loan_assistant.rag.index import run_index

    return run_index()


RUNNERS: dict[str, Callable[[bool], dict[str, Any]]] = {
    "scrape": _scrape,
    "process": _process,
    "chunk": _chunk,
    "index": _index,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the loan knowledge base and index.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="run every step")
    group.add_argument("--step", choices=STEPS, help="run one step")
    parser.add_argument("--refresh", action="store_true", help="scrape: ignore the cache")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for step in STEPS if args.all else (args.step,):
        started = time.perf_counter()
        print(f"== {step}")
        summary = RUNNERS[step](args.refresh)
        short = {k: v for k, v in summary.items() if isinstance(v, int | float | str)}
        print(f"   done in {time.perf_counter() - started:.0f}s: {short}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
