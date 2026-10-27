"""Turn a raw Bank of Maharashtra page into clean Markdown sections.

Two page layouts exist on the site:
* tabbed product pages (``#pane-fb``, ``#pane-ir`` ...): one section per tab;
* plain article pages: sections follow the ``<h2>`` headings.
Everything that is not content (menus, footer, cookie banner, calculators, apply buttons,
scripts) is dropped, tables become Markdown tables, and text is normalized.
"""

import copy
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

DROP_TAGS = (
    "script style noscript svg img picture iframe input select textarea link meta "
    "nav header footer aside canvas video audio map object embed"
).split()
DROP_SELECTORS = [
    ".floatBtns-wrap",
    ".floatBtns",
    ".breadcon",
    ".cook-parent",
    ".similarslide",
    ".btncol",
    ".calcbtnBig",
    ".homesocial",
    ".dvAudioBtn",
    ".social",
    ".nav-tabs",
    "[role=tablist]",
    "#pane-emi",
    "a.applybtn",
    "a.applybtnBig",
    "a.btn1",
    "a.btn2",
    "a.btnmore",
    "a.btn-apply",
    "[hidden]",
    '[style*="display:none"]',
    '[style*="display: none"]',
]
CONTAINERS = (".maincontent", ".contentarea", "main", "#content", "body")
INLINE = set(
    "a span strong b em i u small font sup sub label abbr mark cite code big s strike del ins"
    " q".split()
)
BLOCK_TAGS = {"p", "div", "section", "article", "ul", "ol", "table", "dl", "dt", "dd", "li"} | {
    f"h{n}" for n in range(1, 7)
}
# Whole-block UI text that is never content.
UI_TEXT = re.compile(
    r"^(know more\.*|read more\.*|click here\.?|apply (now|online)|get a call ?back|"
    r"calculate emi|download|view more|show emi|submit|reset|close|back to top|"
    r"\*+|-+|\.+|·|\|)$",
    re.I,
)
# Section headings that are navigation or promotion, not product information.
DROP_HEADINGS = re.compile(
    r"^(similar|related|you may also|other (loans|products)|calculators?|emi calculator|"
    r"share( this)?|apply online|quick links)\b",
    re.I,
)
PSEUDO_HEADING_MAX_WORDS = 8
# Wingdings-style bullet glyphs and invisible characters found in pasted Word content.
JUNK_CHARS = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
CHAR_MAP = {
    ord(" "): " ",
    ord(" "): " ",
    ord(" "): " ",
    ord(" "): " ",
    ord("‘"): "'",
    ord("’"): "'",
    ord("“"): '"',
    ord("”"): '"',
    ord("–"): "-",
    ord("—"): "-",
    ord("•"): "-",
    ord("●"): "-",
    ord("▪"): "-",
    ord("→"): "->",
}


@dataclass
class Section:
    heading: str
    body: str

    @property
    def words(self) -> int:
        return len(self.body.split())


@dataclass
class CleanPage:
    title: str
    sections: list[Section] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(s.words for s in self.sections)

    def to_markdown(self, heading_level: int = 1) -> str:
        """Markdown: ``# title`` then one ``##`` block per section."""
        top = "#" * heading_level
        sub = "#" * (heading_level + 1)
        parts = [f"{top} {self.title}"]
        for s in self.sections:
            parts.append(f"{sub} {s.heading}\n\n{s.body}")
        return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------------------
# Text normalization
# --------------------------------------------------------------------------------------
_RS = re.compile(r"(?:₹|\bRs\b\.?|\bINR\b\.?)\s*(?=\d)", re.I)
_PCT_SPACE = re.compile(r"(\d)\s+%")
_PCT_WORD = re.compile(r"(\d)\s*(?:per\s?cent|percent)\b", re.I)
_PA = re.compile(r"%\s*p\.?\s?a\b\.?", re.I)
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?])(?=\s|$)")


def normalize_text(text: str) -> str:
    """Normalize whitespace, quotes, dashes, rupees (``Rs. 50``) and percents (``7.25% p.a.``)."""
    text = text.translate(JUNK_CHARS).translate(CHAR_MAP)
    text = _RS.sub("Rs. ", text)
    text = _PCT_WORD.sub(r"\1%", text)
    text = _PCT_SPACE.sub(r"\1%", text)
    text = _PA.sub("% p.a.", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = re.sub(r"\.\.(?!\.)", ".", text)  # "p.a.." after a sentence-ending full stop
    return text.strip()


# --------------------------------------------------------------------------------------
# HTML -> blocks
# --------------------------------------------------------------------------------------
UI_SUFFIX = re.compile(r"\s*\b(know more|read more)\b\.*\s*$", re.I)  # only a trailing link label
PARA = chr(10) * 2
END_PUNCT = (".", ";", ":", "!", "?")
Item = tuple  # ("h", level, text) | ("md", text)


def _is_tag(node) -> bool:
    return isinstance(node, Tag)


def inline_text(node) -> str:
    """Text of a node with inline markup flattened; ``<br>`` becomes a line break."""
    return _inline_nodes(node.children)


def _inline_nodes(nodes) -> str:
    return _strip_ui(normalize_text(_raw_inline(nodes)))


def _raw_inline(nodes) -> str:
    """Concatenate text without normalizing, so spaces inside <strong>Minimum </strong> survive."""
    out: list[str] = []
    for child in nodes:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            out.append(str(child))
        elif child.name == "br":
            out.append(chr(10))
        elif child.name == "table":
            out.append(" " + flatten_table(child) + " ")
        elif child.name in ("ul", "ol"):
            out.append(" " + " ".join(f"- {t}" for t in _list_texts(child)) + " ")
        else:
            out.append(_raw_inline(child.children))
            if child.name in BLOCK_TAGS:
                out.append(" ")
    return "".join(out)


def _strip_ui(text: str) -> str:
    return UI_SUFFIX.sub("", text).strip()


def _list_texts(lst: Tag) -> list[str]:
    return [t for li in lst.find_all("li") if (t := inline_text(li))]


def flat_pieces(node: Tag) -> list[str]:
    """Split a container into text pieces: one per block child, inline runs kept together."""
    pieces: list[str] = []
    buf: list = []

    def flush():
        text = _inline_nodes(buf)
        if text:
            pieces.append(text)
        buf.clear()

    for child in node.children:
        if isinstance(child, Comment):
            continue
        if _is_tag(child) and child.name in ("ul", "ol"):
            flush()
            texts = _list_texts(child)
            if texts:
                pieces.append(" ".join(f"- {t}" for t in texts))
        elif _is_tag(child) and child.name in BLOCK_TAGS:
            flush()
            sub = flat_pieces(child) if child.find(list(BLOCK_TAGS)) else [inline_text(child)]
            pieces += [p for p in sub if p]
        else:
            buf.append(child)
    flush()
    return pieces


def cell_text(cell: Tag) -> str:
    """Text of a table cell: paragraphs, list items and nested tables flattened into one line.

    A bold-only short paragraph ("For Category A") labels the paragraph after it ("label: text");
    other paragraphs are joined with "; " unless the earlier one already ends a sentence.
    """
    parts: list[tuple[str, str]] = []  # (text, kind) with kind in text | label | list
    buf: list = []

    def flush():
        text = _inline_nodes(buf)
        buf.clear()
        if text:
            parts.append((text, "text"))

    for child in cell.children:
        name = getattr(child, "name", None)
        if isinstance(child, Comment):
            continue
        if name is None or name in INLINE or name == "br":
            buf.append(child)
            continue
        flush()
        if name == "table":
            parts.append((flatten_table(child), "list"))
        elif name in ("ul", "ol"):
            if items := _list_texts(child):
                parts.append((_join_items(items), "list"))
        elif child.find(["table", "ul", "ol", "p", "div"]):
            if text := cell_text(child):
                parts.append((text, "text"))
        elif label := _pseudo_heading(child):
            parts.append((re.sub(r"[\s:\-]+$", "", label), "label"))
        elif text := inline_text(child):
            parts.append((text, "text"))
    flush()

    out = ""
    prev_kind = ""
    for text, kind in parts:
        if not out:
            out = text
        elif prev_kind == "label":
            out = f"{out}: {text}"
        elif kind == "list" or out.endswith(END_PUNCT):
            out = f"{out} {text}"
        else:
            out = f"{out}; {text}"
        prev_kind = kind
    return out.replace("|", "/").replace(chr(10), " ").strip()


def _join_items(items: list[str]) -> str:
    """Join list items on one line: with a space after a full stop, else with '; '."""
    out = items[0]
    for item in items[1:]:
        out += (" " if out.endswith(END_PUNCT) else "; ") + item
    return out


def _own_rows(table: Tag) -> list[Tag]:
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _span(cell: Tag, attr: str) -> int:
    value = str(cell.get(attr, "1")).strip()
    return max(1, int(value)) if value.isdigit() else 1


def table_grid(table: Tag) -> list[list[str]]:
    """Rows of cell texts with colspan padded and rowspan repeated down."""
    grid: list[list[str]] = []
    pending: dict[int, list] = {}
    for tr in _own_rows(table):
        row: list[str] = []
        cells = iter(tr.find_all(["td", "th"], recursive=False))
        while True:
            while len(row) in pending:
                col = len(row)
                row.append(pending[col][0])
                pending[col][1] -= 1
                if pending[col][1] <= 0:
                    del pending[col]
            cell = next(cells, None)
            if cell is None:
                break
            text = cell_text(cell)
            span, rows_down = _span(cell, "colspan"), _span(cell, "rowspan")
            for k in range(span):
                if rows_down > 1:
                    pending[len(row)] = [text if k == 0 else "", rows_down - 1]
                row.append(text if k == 0 else "")
        if any(c for c in row):
            grid.append(row)
    return grid


def flatten_table(table: Tag) -> str:
    """One-line text for a table nested inside a cell: rows joined by '; ', cells by ' - '."""
    rows = [" - ".join(c for c in r if c) for r in table_grid(table)]
    return _join_items([r for r in rows if r])


def table_markdown(table: Tag) -> str:
    grid = table_grid(table)
    if not grid:
        return ""
    width = max(len(r) for r in grid)
    caption: list[str] = []
    # Leading title rows (a single filled cell above the real header) become plain lines.
    while len(grid) > 1 and sum(bool(c) for c in grid[0]) == 1 and len(grid[1]) >= 2:
        caption.append(next(c for c in grid[0] if c))
        grid.pop(0)
    if width == 1 or (len(grid) == 1 and sum(bool(c) for c in grid[0]) <= 1):
        return "\n".join(caption + [r[0] for r in grid if r and r[0]])
    grid = [r + [""] * (width - len(r)) for r in grid]
    head = [c or " " for c in grid[0]]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * width) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
    return "\n".join(caption + ([""] if caption else []) + lines).strip()


def list_markdown(lst: Tag, depth: int = 0) -> str:
    """Markdown list; nested lists are indented, tables follow their item at column 0.

    A ``ul`` placed directly in a ``ul`` (invalid but common) nests too.
    """
    lines: list[str] = []
    pad = "  " * depth
    n = 0
    for child in lst.children:
        if not _is_tag(child):
            continue
        if child.name in ("ul", "ol"):
            lines.append(list_markdown(child, depth + 1))
            continue
        if child.name != "li":
            continue
        n += 1
        marker = f"{n}." if lst.name == "ol" else "-"
        shell = copy.copy(child)
        blocks = [
            e
            for e in shell.find_all(["ul", "ol", "table"])
            if e.find_parent(["ul", "ol", "table"]) is None
        ]
        for e in blocks:
            e.extract()
        pieces = flat_pieces(shell)
        if len(pieces) == 2 and len(pieces[0].split()) <= 8 and not pieces[0].endswith((".", ":")):
            text = f"{pieces[0]}: {pieces[1]}"
        else:
            text = " ".join(pieces)
        text = text.replace(chr(10), " ")
        if text and lst.name == "ul":
            text = re.sub(r"^\d+\.\s+", "", text)
        if text:
            lines.append(f"{pad}{marker} {text}")
        for e in blocks:
            if e.name == "table":
                md = table_markdown(e)
                if md:  # not indented: table rows always start at column 0
                    lines += ["", md, ""]
            else:
                lines.append(list_markdown(e, depth + 1))
    return chr(10).join(line for line in lines).strip(chr(10))


def _pseudo_heading(p: Tag) -> str | None:
    """A paragraph that is only a short bold phrase acts as a heading (e.g. 'Key Features')."""
    text = inline_text(p)
    bold = "".join(inline_text(b) for b in p.find_all(["strong", "b"]))
    words = len(text.split())
    if not text or not (1 <= words <= PSEUDO_HEADING_MAX_WORDS):
        return None
    if re.sub(r"\W", "", bold) != re.sub(r"\W", "", text) or text.endswith((".", ",", ";")):
        return None
    return text.rstrip(":").strip()


def walk(node: Tag, items: list[Item]) -> None:
    """Append ('h', level, text) and ('md', markdown) items for the content under ``node``."""
    buf: list = []

    def flush():
        text = _inline_nodes(buf)
        buf.clear()
        if text and not UI_TEXT.match(text):
            items.append(("md", text))

    for child in node.children:
        if isinstance(child, Comment):
            continue
        name = getattr(child, "name", None)
        if name is None or name in INLINE or name == "br":
            buf.append(child)
            continue
        flush()
        if re.fullmatch(r"h[1-6]", name):
            text = inline_text(child)
            if text:
                items.append(("h", int(name[1]), text))
        elif name == "p":
            heading = _pseudo_heading(child)
            if heading:
                items.append(("h", 3, heading))
            else:
                text = inline_text(child)
                if text and not UI_TEXT.match(text):
                    items.append(("md", text))
        elif name in ("ul", "ol"):
            md = list_markdown(child)
            if md:
                items.append(("md", md))
        elif name == "table":
            md = table_markdown(child)
            if md:
                items.append(("md", md))
        elif "cont" in (child.get("class") or []) and child.find_parent(class_="intrtcol"):
            if line := _rate_tile(child):
                items.append(("md", line))
            walk(child, items)  # text and lists that sit under the tile
        else:
            walk(child, items)
    flush()


def _rate_tile(cont: Tag) -> str:
    """The highlighted rate tile ('Interest Rate' + '7.25 %P.A*') becomes one line.

    The tile's headings are removed from the tree so the rest of the box can be walked normally.
    """
    heads = [h for h in cont.find_all(["h2", "h3", "h4"]) if not h.find_parent(["h2", "h3", "h4"])]
    bits = [normalize_text(s) for h in heads for s in h.stripped_strings]
    for h in heads:
        h.decompose()
    first_number = next((i for i, b in enumerate(bits) if re.match(r"^\d[\d.,]*$", b)), None)
    if first_number is None or first_number == 0:
        return normalize_text(" ".join(bits))
    return normalize_text(f"{' '.join(bits[:first_number])}: {' '.join(bits[first_number:])}")


# --------------------------------------------------------------------------------------
# Items -> sections
# --------------------------------------------------------------------------------------
def _render(items: list[Item], section_level: int | None) -> tuple[list[Section], list[str]]:
    """Group items into sections. Headings at or above ``section_level`` open a new section;
    deeper headings become ``###``+ lines. Returns (sections, text before the first heading)."""
    sections: list[Section] = []
    intro: list[str] = []
    cur: list[str] | None = None
    heading = ""
    for item in items:
        if item[0] == "h":
            _, level, text = item
            if section_level is not None and level <= section_level:
                if cur is not None:
                    sections.append(Section(heading, "\n\n".join(cur).strip()))
                heading, cur = text.rstrip(":").strip(), []
            else:
                line = f"{'#' * min(6, max(3, level))} {text}"
                (cur if cur is not None else intro).append(line)
        else:
            (cur if cur is not None else intro).append(item[1])
    if cur is not None:
        sections.append(Section(heading, "\n\n".join(cur).strip()))
    return sections, intro


def _promote_captions(items: list[Item]) -> list[Item]:
    """A short line without a full stop right above a table is that table's caption/heading."""
    out: list[Item] = []
    for i, item in enumerate(items):
        nxt = items[i + 1] if i + 1 < len(items) else None
        is_caption = (
            item[0] == "md"
            and nxt is not None
            and nxt[0] == "md"
            and nxt[1].startswith("|")
            and not item[1].startswith(("|", "-"))
            and len(item[1].split()) <= 20
            and not item[1].endswith(END_PUNCT)
        )
        out.append(("h", 3, item[1]) if is_caption else item)
    return out


def _drop(soup: Tag) -> None:
    for sel in DROP_SELECTORS:
        for el in soup.select(sel):
            el.decompose()
    for el in soup.find_all(DROP_TAGS):
        el.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()


def _title(soup: BeautifulSoup, container: Tag) -> str:
    """Page title from the h1 (plus a subtitle ``<h2 class="h4">``); the h1 is removed.

    Some pages leave ``.heading-wrap`` unclosed, so it can wrap the whole article: only the
    heading elements are taken out of it, never the wrapper itself.
    """
    h1 = container.find("h1") or soup.find("h1")
    title = inline_text(h1) if h1 else ""
    if h1:
        sub = h1.find_next_sibling()
        if sub is not None and sub.name == "h2" and "h4" in (sub.get("class") or []):
            if text := inline_text(sub):
                title = f"{title} {text}"
            sub.decompose()
        h1.decompose()
    if not title and soup.title and soup.title.string:
        title = normalize_text(soup.title.string.split("|")[0])
    return title


def _merge_same_heading(sections: list[Section]) -> list[Section]:
    merged: list[Section] = []
    for s in sections:
        prev = next((m for m in merged if m.heading.lower() == s.heading.lower()), None)
        if prev:
            prev.body = f"{prev.body}\n\n{s.body}".strip()
        else:
            merged.append(s)
    return merged


def _only_loan_charges(container: Tag) -> None:
    """service-charges has deposit, locker and forex tables too; keep the advances (loan) block."""
    sections = container.select(".accordion-section")
    if not sections:
        return
    for sec in sections:
        head = sec.get_text(" ", strip=True)[:120]
        if not re.search(r"advances|loan|credit", head, re.I):
            sec.decompose()


def clean_html(html: str, url: str = "") -> CleanPage:
    """Parse one raw page and return its title and ordered Markdown sections."""
    soup = BeautifulSoup(html, "lxml")
    container = next((c for sel in CONTAINERS if (c := soup.select_one(sel))), soup)
    # Tab labels live in <button>s, which _drop removes, so read them first.
    labels = {
        p.get("id"): inline_text(h)
        for p in container.select("[id^=pane-]")
        if (h := p.select_one(".card-header"))
    }
    _drop(container)
    if url.rstrip("/").endswith("/service-charges"):
        _only_loan_charges(container)
    title = _title(soup, container)

    sections: list[Section] = []
    panes = container.select("[id^=pane-]")
    pane_sections: list[Section] = []
    for pane in panes:
        for junk in pane.find_all(class_="card-header", recursive=False):
            junk.decompose()  # the tab's own header; FAQ questions in nested headers stay
        for junk in pane.select("h2.subhead"):
            junk.decompose()
        items: list[Item] = []
        walk(pane, items)
        text = PARA.join(_render(items, None)[1]).strip()
        if text:
            pane_sections.append(Section(labels.get(pane.get("id")) or pane.get("id", ""), text))
        pane.extract()

    items = []
    walk(container, items)
    items = [i for i in items if not (i[0] == "h" and DROP_HEADINGS.match(i[2]))]
    secs, intro = _render(items, None if panes else 2)
    if not panes and not secs:  # no <h2> at all: bold phrases and table captions are the headings
        secs, intro = _render(_promote_captions(items), 3)
    head_text = PARA.join(intro).strip()
    if head_text:
        sections.append(Section("Overview", head_text))
    sections += pane_sections if panes else secs

    sections = [s for s in sections if s.body and not DROP_HEADINGS.match(s.heading)]
    return CleanPage(title=title, sections=_merge_same_heading(sections))


def clean_pdf_text(text: str) -> CleanPage:
    """PDF text has no markup: re-join lines broken mid-sentence and return one section."""
    lines = [normalize_text(line) for line in text.splitlines()]
    lines = [ln for ln in lines if ln.lower() != "restricted"]  # classification stamp on each page
    out: list[str] = []
    for line in lines:
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        open_line = out and out[-1] and not out[-1].endswith((".", ":", ";", "?", "!"))
        if open_line and line[0].islower():
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    first = next((ln for ln in out if ln and not ln.startswith("|")), "")
    if out and out[0] == first:  # the title line is not repeated in the body
        out = out[1:]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out).strip())
    return CleanPage(title=first[:120], sections=[Section("Document text", body)] if body else [])
