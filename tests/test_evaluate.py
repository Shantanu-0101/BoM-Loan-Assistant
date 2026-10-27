import json
from collections import Counter

import pytest

from loan_assistant.config import settings
from loan_assistant.evaluate import (
    CachingProvider,
    JsonCache,
    Judge,
    Question,
    Result,
    comparison_table,
    fact_coverage,
    fact_present,
    hand_grade,
    is_rate_limit,
    keyword_grade,
    load_questions,
    normalize,
    parse_judge,
    render_report,
    summarize,
)
from loan_assistant.rag.generate import Source, link_citations, sources_by_page

# ---- key-fact matching ------------------------------------------------------------------------


def test_normalize_writes_numbers_and_units_one_way():
    assert normalize("Rs. 20.00 Lakhs") == "20 lakh"
    assert normalize("₹7,50,000") == "750000"
    assert normalize("RLLR - 0.80% = 7.50 %") == "rllr - 0.8% = 7.5%"


@pytest.mark.parametrize(
    ("answer", "fact", "expected"),
    [
        ("Maximum Rs. 5.00 Lakh", "5 lakh", True),
        ("Maximum Rs. 25 lakh", "5 lakh", False),  # not a hit inside "25 lakh"
        ("Up to 84 months (7 years)", "84 months | 7 years", True),
        ("Repayment is 7 years", "84 months | 7 years", True),
        ("Repayment is 60 months", "84 months | 7 years", False),
        ("The rate is 7.25% p.a.", "7.25%", True),
        ("The rate is 17.25% p.a.", "7.25%", False),
        ("Rate 7.5 %", "7.5%", True),
        ("Women and Defence personnel", "women", True),
    ],
)
def test_fact_present(answer, fact, expected):
    assert fact_present(answer, fact) is expected


def test_coverage_and_keyword_grade():
    answer = "Up to 5 lakh over 60 months."
    assert fact_coverage(answer, ["5 lakh", "60 months", "15%"]) == pytest.approx(2 / 3)
    assert fact_coverage(answer, []) is None
    assert [keyword_grade(c) for c in (None, 1.0, 0.5, 0.49)] == [None, 2, 1, 0]


# ---- the question set itself ------------------------------------------------------------------


def chunks_by_url() -> dict[str, str]:
    pages: dict[str, str] = {}
    for line in (settings.processed_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines():
        chunk = json.loads(line)
        pages[chunk["url"]] = pages.get(chunk["url"], "") + "\n" + chunk["text"]
    return pages


def test_question_set_has_the_planned_mix():
    questions = load_questions()
    assert len(questions) == 25
    kinds = Counter(q.type for q in questions)
    assert set(kinds) == {"fact", "summary", "out_of_scope"}
    assert kinds["out_of_scope"] == 3 and kinds["fact"] + kinds["summary"] == 22  # 4 PDF + 18
    assert [q.question for q in questions[:4]] == [
        "What are the interest rates for a Bank of Maharashtra home loan?",
        "What is the maximum tenure for a personal loan if my salary account is with the bank?",
        "Tell me about the Maha Super Flexi Housing Loan Scheme.",
        "Are there any processing fee concessions for women or defence personnel on home loans?",
    ]


def test_every_expected_page_is_in_the_knowledge_base():
    pages = chunks_by_url()
    for q in load_questions():
        for url in q.accepted_urls:
            assert url in pages, f"{q.id}: {url} is not in chunks.jsonl"
            assert url.startswith(settings.base_url)


def test_every_key_fact_is_written_on_an_expected_page():
    """The hand-written facts must come from the official pages, not from memory."""
    pages = chunks_by_url()
    for q in load_questions():
        text = "\n".join(pages[u] for u in q.accepted_urls)
        for fact in q.key_facts:
            assert fact_present(text, fact), f"{q.id}: fact {fact!r} not found on its page(s)"


def test_out_of_scope_questions_have_no_facts_or_page():
    for q in load_questions():
        if not q.in_scope:
            assert q.key_facts == [] and q.expected_url == ""
        else:
            assert q.key_facts and q.expected_url


# ---- judge ------------------------------------------------------------------------------------


def test_parse_judge_accepts_json_with_noise_and_rejects_bad_grades():
    assert parse_judge('Sure: {"grade": 2, "reason": "all facts"}') == (2, "all facts")
    assert parse_judge('{"grade": 5, "reason": "x"}') is None
    assert parse_judge("no json here") is None
    assert parse_judge('{"reason": "no grade"}') is None


class CountingProvider:
    def __init__(self, reply: str) -> None:
        self.name = "fake/judge"
        self.reply, self.calls = reply, 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


def question() -> Question:
    return Question("q99", "What is X?", "fact", ["5 lakh"], "https://bankofmaharashtra.bank.in/x")


def test_judge_caches_verdicts(tmp_path):
    provider = CountingProvider('{"grade": 1, "reason": "partly"}')
    judge = Judge(provider, JsonCache(tmp_path / "j.json"), pause_s=0)
    assert judge.grade(question(), "answer") == (1, "partly")
    assert judge.grade(question(), "answer") == (1, "partly")
    assert provider.calls == 1
    again = Judge(provider, JsonCache(tmp_path / "j.json"), pause_s=0)  # new run, same file
    assert again.grade(question(), "answer") == (1, "partly") and provider.calls == 1


def test_judge_gives_up_quietly_on_garbage(tmp_path):
    provider = CountingProvider("not json")
    judge = Judge(provider, JsonCache(tmp_path / "j.json"), pause_s=0)
    assert judge.grade(question(), "answer") is None and provider.calls == 3  # two retries


# ---- answer cache -----------------------------------------------------------------------------


def test_caching_provider_calls_the_api_once_per_prompt(tmp_path):
    inner = CountingProvider("reply")
    cached = CachingProvider(inner, JsonCache(tmp_path / "a.json"), pause_s=0)
    assert cached.complete("sys", "user") == "reply"
    assert cached.last_extra_ms == 0
    assert cached.complete("sys", "user") == "reply"
    assert inner.calls == 1 and cached.last_extra_ms >= 0
    cached.complete("sys", "another prompt")
    assert inner.calls == 2 and cached.name == "fake/judge"


# ---- grades and summary -----------------------------------------------------------------------


def test_hand_grade_prefers_a_mode_row_over_the_wildcard():
    grades = {("q01", "*"): (1, "owner"), ("q01", "vector"): (0, "owner"), ("q02", "*"): (2, "x")}
    assert hand_grade(grades, "q01", "vector") == (0, "owner")
    assert hand_grade(grades, "q01", "hybrid") == (1, "owner")
    assert hand_grade(grades, "q03", "hybrid") is None


def result(qid: str, **kw) -> Result:
    base = dict(
        id=qid, type="fact", question="q", answer="a", refused=False, reason="", provider="p",
        latency_ms=1000, top_cosine=0.8, retrieved_urls=["u"], hit_rank=1, coverage=1.0,
        keyword_grade=2,
    )  # fmt: skip
    return Result(**{**base, **kw})


def test_summary_counts_hits_refusals_and_agreement():
    results = [
        result("q01", hand_grade=2, judge_grade=2),
        result("q02", hit_rank=None, coverage=0.0, keyword_grade=0, hand_grade=0, judge_grade=1),
        result("q03", refused=True, hit_rank=3, hand_grade=0),
        result("q04", type="out_of_scope", refused=True, coverage=None, keyword_grade=None,
               hit_rank=None, hand_grade=2, judge_grade=2),
    ]  # fmt: skip
    s = summarize(results)
    assert (s["hit5"], s["hit1"], s["in_scope"]) == (2, 1, 3)
    assert (s["refused_out"], s["out_scope"], s["wrongly_refused"]) == (1, 1, 1)
    assert (s["hand_correct"], s["hand_wrong"], s["hand_n"]) == (2, 2, 4)
    assert (s["agree"], s["agree_n"]) == (2, 3)
    assert s["latency_s"] == 1.0


def test_report_and_comparison_render():
    q = {"q01": Question("q01", "Q?", "fact", ["7.25%"], "https://bankofmaharashtra.bank.in/p")}
    results = [result("q01", question="Q?", answer="It is 7.25%.", hand_grade=2)]
    text = render_report(results, q, "hybrid", "2026-10-28")
    assert "Evaluation: hybrid retrieval" in text and "> It is 7.25%." in text
    table = comparison_table({"vector": results, "hybrid": results})
    assert table.splitlines()[0] == "| Metric | vector | hybrid |"
    assert "| Retrieval hit@5 | 1/1 (100%) | 1/1 (100%) |" in table


# ---- UI helpers (shared with the CLI) ---------------------------------------------------------


def test_sources_by_page_groups_citations_of_one_page():
    sources = [Source(3, "B", "u2"), Source(1, "A", "u1"), Source(2, "A", "u1")]
    assert sources_by_page(sources) == [("u1", "A", [1, 2]), ("u2", "B", [3])]


def test_link_citations_links_known_numbers_only():
    sources = [Source(1, "A", "https://x/a"), Source(2, "B", "https://x/b")]
    text = link_citations("Rate 7% [1][2] and 8% [7]. See [1](https://x/a).", sources)
    assert "[\\[1\\]](https://x/a)[\\[2\\]](https://x/b)" in text
    assert "[7]" in text and text.count("https://x/a") == 2  # the existing link is not re-linked


class RateLimitedOnce:
    name = "fake/groq"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Error code: 429 - rate_limit_exceeded, try again in 1s")
        return "ok"


def test_rate_limited_call_is_retried_and_other_errors_are_not(tmp_path):
    flaky = RateLimitedOnce()
    cached = CachingProvider(flaky, JsonCache(tmp_path / "a.json"), pause_s=0, retry_wait_s=0)
    assert cached.complete("s", "u") == "ok" and flaky.calls == 2
    assert cached.last_wait_ms == 0  # retry_wait_s was 0
    assert is_rate_limit(RuntimeError("HTTP 429")) and not is_rate_limit(TimeoutError("slow"))
    broken = CachingProvider(CountingProvider("x"), JsonCache(tmp_path / "b.json"), pause_s=0)
    broken.inner.complete = lambda s, u: (_ for _ in ()).throw(TimeoutError("boom"))
    with pytest.raises(TimeoutError):
        broken.complete("s", "u")
