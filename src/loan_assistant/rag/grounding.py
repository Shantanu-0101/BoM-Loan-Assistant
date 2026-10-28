"""Drop figures the model wrote that are not in the bank's text it was given.

Prompt rule 3 forbids arithmetic, but a model still sometimes writes "7.20%" after subtracting
0.05 from 7.25. A number that appears neither in the retrieved chunks nor in the question has no
source, so the sentence that carries it is removed and the answer says so.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

NUMBER = re.compile(r"\d+(?:,\d{2,3})*(?:\.\d+)?")  # 7.25   2,00,000   750
CITATION_MARK = re.compile(r"\[\d+(?:\s*[,;]\s*\d+)*\]")
URL = re.compile(r"https?://\S+")
DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b")  # 31.12.2025 is a date, not 31.12
PARENTHESIS = re.compile(r"\s*\([^()]*\)")
LIST_MARKER = re.compile(r"^\s*(?:[-*]\s+)?\d+[.)]\s+")  # "1. " at the start of a line
# A small whole number is only checked when a unit follows ("3 years", "5 lakh", "2 %").
UNIT_AFTER = re.compile(r"\s*(?:%|(?:lakhs?|crores?|years?|yrs?|months?|days?|emis?|bps)\b)", re.I)
SENTENCE_BREAK = re.compile(r"(?<=[.!?\]])\s+(?=[A-Z*_(\"“])")  # a citation may end a sentence
DISCLAIMER_MARK = re.compile(r"not financial advice|information as of", re.I)

NOTE = (
    "_Note: one figure in the first draft of this answer is not written on the bank's pages, "
    "so it was left out._"
)


@dataclass
class Grounded:
    text: str
    removed: list[str] = field(default_factory=list)  # the unsupported figures, as written
    emptied: bool = False  # nothing cited was left after removing them


def _value(token: str) -> Decimal:
    return Decimal(token.replace(",", ""))


def numbers_in(text: str) -> set[Decimal]:
    """Every number written in ``text`` (7.2 and 7.20 are the same number)."""
    return {_value(m.group(0)) for m in NUMBER.finditer(DATE.sub(r"\1 \2 \3", text))}


def _checked_numbers(line: str) -> list[tuple[str, Decimal]]:
    """Figures in an answer line that need a source: not citations, URLs or list numbers."""
    line = LIST_MARKER.sub("", URL.sub(" ", CITATION_MARK.sub(" ", line)))
    line = DATE.sub(r"\1 \2 \3", line)
    found = []
    for m in NUMBER.finditer(line):
        value = _value(m.group(0))
        if value == 0:  # "0%" is how a model writes the bank's "zero interest"
            continue
        small_whole = "." not in m.group(0) and "," not in m.group(0) and value < 10
        if small_whole and not UNIT_AFTER.match(line, m.end()):
            continue
        found.append((m.group(0), value))
    return found


def _split(line: str) -> list[str]:
    if line.lstrip().startswith("|"):  # a table row is one unit
        return [line]
    return SENTENCE_BREAK.split(line)


def _unknown(text: str, known: set[Decimal]) -> list[str]:
    return [tok for tok, value in _checked_numbers(text) if value not in known]


def _repair(sentence: str, known: set[Decimal]) -> tuple[str, list[str]]:
    """The sentence without its unsupported figures; empty when nothing true is left.

    A figure inside a parenthesis is usually the model's own conversion ("180 months (15
    years)"), so only that aside goes. Otherwise the whole sentence is dropped.
    """
    missing = _unknown(sentence, known)
    if not missing:
        return sentence, []
    stripped = PARENTHESIS.sub(
        lambda m: "" if _unknown(m.group(0), known) else m.group(0), sentence
    )
    if stripped != sentence and not _unknown(stripped, known):
        return stripped, missing
    return "", missing


def ground_answer(answer: str, context: Iterable[str], question: str = "") -> Grounded:
    """Remove each sentence that has a figure found neither in ``context`` nor in ``question``."""
    known = numbers_in(question)
    for text in context:
        known |= numbers_in(text)

    removed: list[str] = []
    kept_lines: list[str] = []
    for line in answer.split("\n"):
        if DISCLAIMER_MARK.search(line) or not line.strip():
            kept_lines.append(line)
            continue
        kept = []
        for sentence in _split(line):
            fixed, missing = _repair(sentence, known)
            removed.extend(missing)
            if fixed:
                kept.append(fixed)
        if kept:  # a line with no sentence left is dropped
            kept_lines.append(" ".join(kept))

    if not removed:
        return Grounded(answer)

    last_disclaimer = max(
        (i for i, ln in enumerate(kept_lines) if DISCLAIMER_MARK.search(ln)), default=None
    )
    if last_disclaimer is None:
        kept_lines += ["", NOTE]
    else:
        kept_lines[last_disclaimer:last_disclaimer] = [NOTE, ""]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()
    body = "\n".join(ln for ln in kept_lines if not DISCLAIMER_MARK.search(ln) and ln != NOTE)
    return Grounded(text, removed, emptied=not CITATION_MARK.search(body))
