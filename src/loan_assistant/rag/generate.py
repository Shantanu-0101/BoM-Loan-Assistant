"""Turn retrieved chunks into a cited answer with the LLM."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from loan_assistant.llm.providers import LLMProvider, complete_with_fallback
from loan_assistant.rag.prompt import DISCLAIMER, SYSTEM_PROMPT, build_user_prompt
from loan_assistant.rag.retrieve import RetrievedChunk

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
CITATION = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


@dataclass(frozen=True)
class Source:
    n: int  # the [n] used in the answer text
    title: str
    url: str


def clean_model_text(text: str) -> str:
    return text.translate(TEXT_FIXES).strip()


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
    return with_disclaimer(clean_model_text(text), data_as_of), provider
