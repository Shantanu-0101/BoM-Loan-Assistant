"""Split the knowledge base into retrieval chunks.

Rules (spec FR-C1):
* chunks never cross a section boundary;
* target ~300 words, hard maximum 450, ~50 words of overlap between neighbouring prose chunks;
* a Markdown table is never cut mid-row: long tables are split into row groups and the header
  row is repeated on each group;
* every chunk starts with a context header, so a bare "84 months" still says which product and
  section it belongs to.
"""

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loan_assistant.config import settings
from loan_assistant.scraping.discover import slugify

log = logging.getLogger(__name__)

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s")
TABLE_SEPARATOR = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
PARA_BREAK = "\n\n"


@dataclass
class Chunk:
    id: str
    text: str
    product: str
    section: str
    url: str
    category: str
    scraped_at: str
    type: str
    words: int  # words in the body, without the context header


@dataclass
class Atom:
    """The smallest piece the packer moves around: a sentence, a list item, a table row group."""

    text: str
    words: int
    kind: str  # prose | table
    joiner: str = PARA_BREAK  # what goes between the previous atom and this one


def count_words(text: str) -> int:
    return len(text.split())


def context_header(product: str, section: str, url: str) -> str:
    return f"Product: {product} | Section: {section} | Source: {url}"


def _split_long_text(text: str, limit: int) -> list[str]:
    """Cut text that has no better break point into pieces of at most ``limit`` words."""
    words = text.split()
    return [" ".join(words[i : i + limit]) for i in range(0, len(words), limit)]


def _table_atoms(lines: list[str], target: int, maximum: int) -> list[Atom]:
    """Row groups of a table, each with the header repeated, never splitting a row."""
    header: list[str] = []
    rows = lines
    if len(lines) >= 2 and TABLE_SEPARATOR.match(lines[1].strip()):
        header, rows = lines[:2], lines[2:]
    header_words = count_words(" ".join(header))

    groups: list[list[str]] = []
    current: list[str] = []
    current_words = header_words
    for row in rows:
        row_words = count_words(row)
        if current and current_words + row_words > target:
            groups.append(current)
            current, current_words = [], header_words
        current.append(row)
        current_words += row_words
    if current:
        groups.append(current)

    atoms = []
    for group in groups or [[]]:
        text = "\n".join([*header, *group])
        words = count_words(text)
        if words > maximum:  # a single row that is longer than the limit: cut it (rare)
            for piece in _split_long_text(text, maximum):
                atoms.append(Atom(piece, count_words(piece), "table"))
        else:
            atoms.append(Atom(text, words, "table"))
    return atoms


def _prose_atoms(text: str, target: int) -> list[Atom]:
    """A paragraph is one atom if it fits; otherwise one atom per sentence."""
    words = count_words(text)
    if words <= target:
        return [Atom(text, words, "prose")]
    atoms: list[Atom] = []
    for sentence in SENTENCE_END.split(text):
        pieces = (
            _split_long_text(sentence, target) if count_words(sentence) > target else [sentence]
        )
        for piece in pieces:
            joiner = " " if atoms else PARA_BREAK
            atoms.append(Atom(piece, count_words(piece), "prose", joiner))
    return atoms


def _list_atoms(lines: list[str], target: int) -> list[Atom]:
    """One atom per list item (continuation lines stay with their item)."""
    items: list[str] = []
    for line in lines:
        if LIST_ITEM.match(line) or not items:
            items.append(line)
        else:
            items[-1] += " " + line.strip()
    atoms: list[Atom] = []
    for item in items:
        joiner = "\n" if atoms else PARA_BREAK
        for i, piece in enumerate(
            _split_long_text(item, target) if count_words(item) > target else [item]
        ):
            atoms.append(Atom(piece, count_words(piece), "prose", joiner if i == 0 else " "))
    return atoms


def section_atoms(text: str, target: int, maximum: int) -> list[Atom]:
    """Break a section's Markdown into atoms. A ``###`` heading sticks to what follows it."""
    atoms: list[Atom] = []
    pending_heading = ""
    for block in re.split(r"\n\s*\n", text.strip()):
        # A block can mix text lines and table lines (a table that follows a list item).
        runs: list[tuple[bool, list[str]]] = []
        for line in block.split("\n"):
            if not line.strip():
                continue
            is_table = line.lstrip().startswith("|")
            if runs and runs[-1][0] == is_table:
                runs[-1][1].append(line)
            else:
                runs.append((is_table, [line]))
        for is_table, lines in runs:
            if not is_table and len(lines) == 1 and lines[0].startswith("###"):
                pending_heading = (
                    lines[0] if not pending_heading else pending_heading + "\n" + lines[0]
                )
                continue
            if is_table:
                new = _table_atoms(lines, target, maximum)
            elif LIST_ITEM.match(lines[0]):
                new = _list_atoms(lines, target)
            else:
                new = _prose_atoms(" ".join(line.strip() for line in lines), target)
            if pending_heading and new:
                first = new[0]
                first.text = f"{pending_heading}\n{first.text}"
                first.words = count_words(first.text)
                pending_heading = ""
            atoms.extend(new)
    if pending_heading:
        atoms.append(Atom(pending_heading, count_words(pending_heading), "prose"))
    return atoms


def _join(atoms: list[Atom]) -> str:
    out = ""
    for i, atom in enumerate(atoms):
        out += (atom.joiner if i else "") + atom.text
    return out


def _overlap_tail(atoms: list[Atom], overlap: int) -> list[Atom]:
    """Trailing prose atoms (at most ``overlap`` words) to repeat at the start of the next chunk."""
    tail: list[Atom] = []
    total = 0
    for atom in reversed(atoms):
        if atom.kind != "prose" or total + atom.words > overlap:
            break
        tail.insert(0, atom)
        total += atom.words
    return tail


def pack_atoms(atoms: list[Atom], target: int, maximum: int, overlap: int) -> list[str]:
    """Greedily fill chunks up to ``target`` words; repeat a short prose tail between chunks."""
    bodies: list[str] = []
    current: list[Atom] = []
    words = 0
    fresh = 0  # atoms in ``current`` that are not just carried-over overlap
    for atom in atoms:
        if current and words + atom.words > target:
            if fresh:
                bodies.append(_join(current))
            tail = _overlap_tail(current, overlap) if fresh else []
            if sum(a.words for a in tail) + atom.words > maximum:
                tail = []
            current, words, fresh = list(tail), sum(a.words for a in tail), 0
        current.append(atom)
        words += atom.words
        fresh += 1
    if fresh:
        bodies.append(_join(current))
    return bodies


def chunk_section(
    product: str,
    section: str,
    text: str,
    url: str,
    target: int | None = None,
    maximum: int | None = None,
    overlap: int | None = None,
) -> list[tuple[str, str]]:
    """Return ``(full_text_with_header, body)`` for each chunk of one section."""
    target = target or settings.chunk_words
    maximum = maximum or settings.chunk_max_words
    overlap = settings.chunk_overlap_words if overlap is None else overlap
    header = context_header(product, section, url)
    atoms = section_atoms(text, target, maximum)
    return [(f"{header}\n\n{body}", body) for body in pack_atoms(atoms, target, maximum, overlap)]


def chunk_product(record: dict[str, Any], **sizes: int) -> list[Chunk]:
    slug = slugify(record["url"])
    chunks: list[Chunk] = []
    used: dict[str, int] = {}
    for section, text in record["sections"].items():
        section_slug = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")[:40] or "section"
        base = f"{slug}#{section_slug}"
        used[base] = used.get(base, 0) + 1
        if used[base] > 1:  # two sections with the same heading on one page
            base = f"{base}-{used[base]}"
        for i, (full, body) in enumerate(
            chunk_section(record["product"], section, text, record["url"], **sizes)
        ):
            chunks.append(
                Chunk(
                    id=f"{base}#{i}",
                    text=full,
                    product=record["product"],
                    section=section,
                    url=record["url"],
                    category=record["category"],
                    scraped_at=record["scraped_at"],
                    type=record.get("type", "html"),
                    words=count_words(body),
                )
            )
    return chunks


def chunks_path() -> Path:
    return settings.processed_dir / "chunks.jsonl"


def run_chunk() -> dict[str, Any]:
    """Read ``loan_products.jsonl`` and write ``chunks.jsonl``; return a short summary."""
    products_file = settings.processed_dir / "loan_products.jsonl"
    records = [
        json.loads(line) for line in products_file.read_text(encoding="utf-8").splitlines() if line
    ]
    chunks = [c for rec in records for c in chunk_product(rec)]
    ids = [c.id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chunk ids")
    with chunks_path().open("w", encoding="utf-8", newline="\n") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    sizes = sorted(c.words for c in chunks)
    summary = {
        "products": len(records),
        "chunks": len(chunks),
        "words_min": sizes[0],
        "words_median": sizes[len(sizes) // 2],
        "words_max": sizes[-1],
        "over_max": sum(1 for s in sizes if s > settings.chunk_max_words),
    }
    log.info("chunks: %s", summary)
    return summary


def main() -> None:
    argparse.ArgumentParser(description="Split the knowledge base into chunks.").parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for key, value in run_chunk().items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
