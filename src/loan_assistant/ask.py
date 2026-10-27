"""Command line: ``python -m loan_assistant.ask "your question" [--show-context]``."""

import argparse
import logging
import sys

from loan_assistant.config import settings
from loan_assistant.llm.providers import ProviderError
from loan_assistant.rag.generate import sources_by_page
from loan_assistant.rag.pipeline import Answer, Pipeline


def format_answer(answer: Answer, show_context: bool = False) -> str:
    lines = [answer.text.strip(), ""]
    if answer.sources:
        lines.append("Sources:")
        for url, title, numbers in sources_by_page(answer.sources):  # one line per page
            lines.append(f"  {''.join(f'[{n}]' for n in numbers)} {title} - {url}")
        lines.append("")
    if show_context:
        lines.append("Retrieved context:")
        for n, rc in enumerate(answer.chunks, start=1):
            c = rc.chunk
            lines.append(
                f"  [{n}] score={rc.score:.3f} cosine={rc.vector_score:.3f} "
                f"vector_rank={rc.vector_rank} bm25_rank={rc.bm25_rank} id={c['id']}"
            )
        lines.append("")
    status = "refused: " + answer.reason if answer.refused else "answered"
    lines.append(
        f"({status}; provider: {answer.provider}; {answer.latency_ms} ms; "
        f"data as of {answer.data_as_of.isoformat()})"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask a question about Bank of Maharashtra loans.")
    parser.add_argument("question", help="the question, in quotes")
    parser.add_argument("--show-context", action="store_true", help="also print retrieved chunks")
    parser.add_argument("--mode", choices=["vector", "hybrid"], default=settings.retrieval_mode)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows console
    try:
        answer = Pipeline().ask(args.question, mode=args.mode)
    except ProviderError as exc:
        print(f"Could not get an answer from the language model: {exc}", file=sys.stderr)
        return 1
    print(format_answer(answer, args.show_context))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
