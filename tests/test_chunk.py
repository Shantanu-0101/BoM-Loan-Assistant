import json

import pytest

from loan_assistant.processing.chunk import (
    chunk_product,
    chunk_section,
    context_header,
    count_words,
)

URL = "https://bankofmaharashtra.bank.in/home-loan"


def make_table(rows: int, words_per_row: int = 10) -> str:
    head = "| Tenure | Rate |\n|---|---|"
    body = [f"| row{i} {'w ' * words_per_row}| {i}.25% |" for i in range(rows)]
    return "\n".join([head, *body])


def run(text: str, **kw: int) -> list[tuple[str, str]]:
    return chunk_section("Home Loan", "Interest Rates", text, URL, **kw)


def test_every_chunk_starts_with_the_context_header():
    chunks = run("Short paragraph about rates.")
    assert len(chunks) == 1
    full, body = chunks[0]
    assert full.startswith(context_header("Home Loan", "Interest Rates", URL) + "\n\n")
    assert body == "Short paragraph about rates."


def test_small_section_is_one_chunk():
    assert len(run("One. Two. Three.")) == 1


def test_long_text_respects_target_and_max():
    sentences = " ".join(f"This is sentence number {i} about the loan." for i in range(200))
    chunks = run(sentences, target=100, maximum=150, overlap=20)
    assert len(chunks) > 5
    for _, body in chunks:
        assert count_words(body) <= 150


def test_neighbouring_prose_chunks_overlap():
    sentences = " ".join(f"Sentence {i} is here." for i in range(120))
    chunks = run(sentences, target=60, maximum=90, overlap=20)
    first_end = chunks[0][1].split(". ")[-1]
    assert first_end in chunks[1][1]


def test_no_overlap_when_disabled():
    sentences = " ".join(f"Sentence {i} is here." for i in range(120))
    chunks = run(sentences, target=60, maximum=90, overlap=0)
    seen = [s for _, body in chunks for s in body.split(". ")]
    assert len(seen) == len(set(seen))


def test_table_is_never_split_mid_row_and_repeats_its_header():
    table = make_table(rows=60, words_per_row=12)
    chunks = run(table, target=100, maximum=150)
    assert len(chunks) > 3
    rows_seen = []
    for _, body in chunks:
        lines = body.split("\n")
        assert lines[0] == "| Tenure | Rate |"
        assert lines[1] == "|---|---|"
        for line in lines[2:]:
            assert line.startswith("| row") and line.endswith("% |")  # a whole row
            rows_seen.append(line)
        assert count_words(body) <= 150
    assert rows_seen == table.split("\n")[2:]  # every row exactly once, in order


def test_short_table_stays_in_one_chunk():
    chunks = run(make_table(rows=3))
    assert len(chunks) == 1
    assert chunks[0][1] == make_table(rows=3)


def test_a_single_row_longer_than_the_limit_is_cut_rather_than_dropped():
    table = "| A | B |\n|---|---|\n| " + "word " * 300 + "| x |"
    chunks = run(table, target=100, maximum=120)
    assert all(count_words(body) <= 120 for _, body in chunks)
    assert sum(body.count("word") for _, body in chunks) == 300


def test_table_after_a_list_item_in_the_same_block_is_kept_as_a_table():
    text = "- Item with rates:\n| A | B |\n|---|---|\n| 1 | 2 |"
    ((_, body),) = run(text)
    assert body == "- Item with rates:\n\n| A | B |\n|---|---|\n| 1 | 2 |"


def test_sub_heading_stays_with_the_text_after_it():
    text = "### Women borrowers\n\nA concession of 0.05% applies."
    ((_, body),) = run(text)
    assert body == "### Women borrowers\nA concession of 0.05% applies."


def test_list_items_are_not_cut_in_half_and_keep_line_breaks():
    items = [f"- Item {i} " + "text " * 20 for i in range(30)]
    chunks = run("\n".join(items), target=100, maximum=140, overlap=0)
    assert len(chunks) > 3
    for _, body in chunks:
        for line in body.split("\n"):
            assert line.startswith("- Item ")
    assert sum(len(body.split("\n")) for _, body in chunks) == 30


def test_chunk_ids_are_unique_and_sections_do_not_mix():
    record = {
        "product": "Home Loan",
        "category": "home",
        "url": URL,
        "scraped_at": "2026-10-27T18:59:00+00:00",
        "type": "html",
        "sections": {
            "FAQs": "Q one. " * 300,
            "Eligibility": "Age 21 to 65.",
            "Overview": "First.",
        },
    }
    chunks = chunk_product(record)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))
    assert {c.section for c in chunks} == {"FAQs", "Eligibility", "Overview"}
    for c in chunks:
        assert c.text.startswith(f"Product: Home Loan | Section: {c.section} | Source: {URL}")
        assert c.url == URL and c.category == "home"
    assert [c for c in chunks if c.section == "Eligibility"][0].text.endswith("Age 21 to 65.")


def test_headings_that_slugify_the_same_get_distinct_ids():
    record = {
        "product": "P",
        "category": "home",
        "url": URL,
        "scraped_at": "x",
        "sections": {"FAQs": "a b c", "FAQs?": "d e f"},
    }
    ids = [c.id for c in chunk_product(record)]
    assert len(ids) == len(set(ids)) == 2


@pytest.mark.parametrize("name", ["chunks.jsonl"])
def test_committed_chunks_obey_the_limits(name):
    from loan_assistant.config import settings

    path = settings.processed_dir / name
    if not path.exists():
        pytest.skip("chunks not built yet")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert len({r["id"] for r in rows}) == len(rows)
    assert max(r["words"] for r in rows) <= settings.chunk_max_words
    for r in rows:
        assert r["text"].startswith(f"Product: {r['product']} | Section: {r['section']} | Source: ")
