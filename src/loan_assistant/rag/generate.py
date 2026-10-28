"""Turn retrieved chunks into a cited answer with the LLM."""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from loan_assistant.llm.providers import LLMProvider, complete_with_fallback
from loan_assistant.rag.grounding import ground_answer
from loan_assistant.rag.prompt import DISCLAIMER, NOT_FOUND, SYSTEM_PROMPT, build_user_prompt
from loan_assistant.rag.retrieve import RetrievedChunk

log = logging.getLogger(__name__)

# The model sometimes writes full-width brackets (U+3010/U+3011, U+FF3B/U+FF3D) around
# citations, plus non-breaking hyphens and spaces (U+2011, U+202F, U+00A0): make them ASCII.
TEXT_FIXES = str.maketrans(
    {
        "【": "[",
        "】": "]",
        "［": "[",
        "］": "]",
        "‑": "-",
        " ": " ",
        " ": " ",
    }
)
# The model sometimes adds a line reference after the number: [1†L4-L5] or [2†line11].
LINE_REFERENCE = re.compile(r"\[(\d+)†[^\]]*\]")
CITATION = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


@dataclass(frozen=True)
class Source:
    n: int  # the [n] used in the answer text
    title: str
    url: str


def clean_model_text(text: str) -> str:
    return LINE_REFERENCE.sub(r"[\1]", text.translate(TEXT_FIXES)).strip()


def cited_numbers(text: str) -> list[int]:
    """Citation numbers in order of first appearance: ``[2][1, 3]`` gives 2, 1, 3."""
    seen: list[int] = []
    for group in CITATION.findall(text):
        for part in re.split(r"[,;]", group):
            n = int(part)
            if n not in seen:
                seen.append(n)
    return seen


def sources_for(text: str, chunks: Sequence[RetrievedChunk]) -> list[Source]:
    """The pages the answer cites. A citation of a block that does not exist is ignored."""
    numbers = [n for n in cited_numbers(text) if 1 <= n <= len(chunks)]
    return [Source(n, chunks[n - 1].chunk["product"], chunks[n - 1].chunk["url"]) for n in numbers]


def sources_by_page(sources: Sequence[Source]) -> list[tuple[str, str, list[int]]]:
    """One ``(url, title, [n, ...])`` entry per page, in the order of first citation."""
    pages: dict[str, tuple[str, list[int]]] = {}
    for s in sorted(sources, key=lambda s: s.n):
        pages.setdefault(s.url, (s.title, []))[1].append(s.n)
    return [(url, title, numbers) for url, (title, numbers) in pages.items()]


def link_citations(text: str, sources: Sequence[Source]) -> str:
    """Turn each single ``[n]`` into a Markdown link to its page (for the web UI)."""
    urls = {s.n: s.url for s in sources}

    def link(match: re.Match[str]) -> str:
        n = int(match.group(1))
        return f"[\\[{n}\\]]({urls[n]})" if n in urls else match.group(0)

    return re.sub(r"\[(\d+)\](?!\()", link, text)


def with_disclaimer(text: str, data_as_of: date) -> str:
    """Make sure the closing disclaimer is there, whatever the model did."""
    if "not financial advice" in text.lower():
        return text
    return f"{text.rstrip()}\n\n{DISCLAIMER.format(data_as_of=data_as_of.isoformat())}"


def generate_answer(
    question: str,
    chunks: Sequence[RetrievedChunk],
    data_as_of: date,
    providers: list[LLMProvider],
) -> tuple[str, str]:
    """Ask the LLM; returns ``(answer_text, provider_name)``."""
    user = build_user_prompt(question, chunks, data_as_of)
    text, provider = complete_with_fallback(providers, SYSTEM_PROMPT, user)
    grounded = ground_answer(clean_model_text(text), [rc.chunk["text"] for rc in chunks], question)
    if grounded.removed:
        log.warning("figures not in the context were removed: %s", ", ".join(grounded.removed))
    if grounded.emptied:  # nothing the bank's pages support was left
        return with_disclaimer(NOT_FOUND, data_as_of), provider
    return with_disclaimer(grounded.text, data_as_of), provider
