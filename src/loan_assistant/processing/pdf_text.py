"""Text extraction for the few PDFs that enter the knowledge base, with tables kept as rows.

Plain ``page.get_text()`` prints a table one cell per line (``Housing`` / ``7.05`` / ``9.55``),
which loses which number belongs to which row. Here the words inside a detected table are
regrouped by vertical position, so each table row stays on one line.
"""

from pathlib import Path

import pymupdf

ROW_TOLERANCE = 3  # points: words whose vertical centres are this close share a row
CELL_GAP = 8  # points: a horizontal gap wider than this starts a new cell


def _table_rows(page: pymupdf.Page, bbox) -> list[str]:
    x0, y0, x1, y1 = bbox
    words = [w for w in page.get_text("words") if y0 - 2 <= w[1] <= y1 + 2 and x0 - 2 <= w[0]]
    rows: list[list] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        centre = (w[1] + w[3]) / 2
        if rows and abs(rows[-1][0] - centre) <= ROW_TOLERANCE:
            rows[-1][1].append(w)
        else:
            rows.append([centre, [w]])
    lines = []
    for _, ws in rows:
        ws.sort(key=lambda w: w[0])
        cells = [[ws[0]]]
        for w in ws[1:]:
            if w[0] - cells[-1][-1][2] > CELL_GAP:
                cells.append([w])
            else:
                cells[-1].append(w)
        lines.append("| " + " | ".join(" ".join(w[4] for w in c) for c in cells) + " |")
    return lines


def pdf_to_text(path: Path) -> str:
    """Text of a PDF in reading order; table regions are emitted as pipe-separated rows."""
    parts: list[str] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            tables = page.find_tables().tables
            boxes = [pymupdf.Rect(t.bbox) for t in tables]
            placed: list[tuple[float, str]] = []
            for block in page.get_text("blocks"):
                if block[6] == 0 and not any(pymupdf.Rect(block[:4]).intersects(b) for b in boxes):
                    placed.append((block[1], block[4].strip()))
            for table in tables:
                placed.append((table.bbox[1], "\n".join(_table_rows(page, table.bbox))))
            parts += [text for _, text in sorted(placed) if text]
    return "\n\n".join(parts)
