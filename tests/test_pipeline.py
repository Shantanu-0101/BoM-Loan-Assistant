from dataclasses import dataclass

import pytest

from loan_assistant.llm.providers import ProviderError, complete_with_fallback
from loan_assistant.rag.generate import (
    cited_numbers,
    clean_model_text,
    sources_for,
    with_disclaimer,
)
from loan_assistant.rag.pipeline import Pipeline, other_bank_mentioned
from loan_assistant.rag.prompt import NOT_FOUND, SYSTEM_PROMPT, build_context
from loan_assistant.rag.retrieve import RetrievedChunk

SCRAPED = "2026-10-27T18:59:14+00:00"  # 28 Oct 2026 in IST


def make_chunk(n: int, url: str = "https://bankofmaharashtra.bank.in/p") -> dict:
    return {
        "id": f"p#s#{n}",
        "text": f"Product: P{n} | Section: S | Source: {url}\n\nBody {n}. Rate 7.25% p.a.",
        "product": f"Product {n}",
        "section": "S",
        "url": f"{url}{n}",
        "category": "home",
        "scraped_at": SCRAPED,
    }


def retrieved(n: int, cosine: float = 0.8) -> RetrievedChunk:
    return RetrievedChunk(make_chunk(n), 0.03, cosine, n, None)


@dataclass
class FakeRetriever:
    cosine: float = 0.8

    def __post_init__(self) -> None:
        self.chunks = [make_chunk(i) for i in range(1, 6)]
        self.calls: list[str] = []

    def search(self, question: str, mode: str | None = None, top_k: int | None = None):
        self.calls.append(question)
        return [retrieved(i, self.cosine) for i in range(1, 6)]


class FakeProvider:
    def __init__(self, reply: str = "Rate is 7.25% [2]. Not financial advice.", fail: bool = False):
        self.name = "fake/model"
        self.reply, self.fail = reply, fail
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if self.fail:
            raise TimeoutError("boom")
        return self.reply


# ---- citations --------------------------------------------------------------------------


def test_cited_numbers_handles_groups_and_repeats():
    assert cited_numbers("A [2]. B [1, 3][2]. C [3]") == [2, 1, 3]
    assert cited_numbers("no citations") == []


def test_model_bracket_variants_are_turned_into_plain_citations():
    assert (
        clean_model_text("Rate 7.25 %【2】 for 30‑year loans") == "Rate 7.25 %[2] for 30-year loans"
    )


def test_sources_ignore_numbers_that_do_not_exist():
    sources = sources_for("x [1] y [9] z [3]", [retrieved(1), retrieved(2), retrieved(3)])
    assert [(s.n, s.title) for s in sources] == [(1, "Product 1"), (3, "Product 3")]


def test_disclaimer_added_only_when_missing():
    from datetime import date

    d = date(2026, 10, 28)
    assert with_disclaimer("Answer.", d).endswith(
        "Information as of 2026-10-28; rates may change. This is not financial advice."
    )
    already = "Answer. Information as of x; This is not financial advice."
    assert with_disclaimer(already, d) == already


def test_context_blocks_are_numbered_from_one():
    context = build_context([retrieved(1), retrieved(2)])
    assert context.startswith("[1] Product: P1") and "\n\n[2] Product: P2" in context


def test_system_prompt_carries_the_spec_rules():
    for phrase in ("ONLY from the numbered context", "Never guess numbers", "[n]", NOT_FOUND):
        assert phrase in SYSTEM_PROMPT


# ---- guardrails -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    ["What is the SBI home loan rate?", "HDFC personal loan rate?", "icici car loan emi"],
)
def test_other_banks_are_refused_without_calling_retriever_or_llm(question):
    retriever, provider = FakeRetriever(), FakeProvider()
    answer = Pipeline(retriever, [provider]).ask(question)
    assert answer.refused and answer.reason == "other-bank"
    assert retriever.calls == [] and provider.prompts == []
    assert answer.provider == "none" and answer.sources == []
    assert NOT_FOUND in answer.text and "not financial advice" in answer.text.lower()


def test_comparison_that_names_our_bank_is_not_blocked():
    assert not other_bank_mentioned("Compare Bank of Maharashtra and SBI home loan rates")
    assert not other_bank_mentioned("Maha Super Housing Loan rate")
    assert other_bank_mentioned("SBI home loan")


def test_low_retrieval_score_is_refused_without_calling_the_llm():
    provider = FakeProvider()
    answer = Pipeline(FakeRetriever(cosine=0.5), [provider]).ask("What's the weather in Pune?")
    assert answer.refused and answer.reason == "low-score"
    assert provider.prompts == []
    assert answer.top_score == pytest.approx(0.5)


def test_empty_question_is_refused():
    answer = Pipeline(FakeRetriever(), [FakeProvider()]).ask("   ")
    assert answer.refused and answer.reason == "empty"


# ---- generation (mocked LLM) -----------------------------------------------------------


def test_answer_has_text_sources_provider_and_date():
    provider = FakeProvider("The rate is 7.25% p.a. [2].")
    answer = Pipeline(FakeRetriever(), [provider]).ask("What is the home loan rate?")
    assert not answer.refused
    assert answer.provider == "fake/model"
    assert [s.n for s in answer.sources] == [2] and answer.sources[0].url.endswith("/p2")
    assert answer.data_as_of.isoformat() == "2026-10-28"
    assert answer.text.endswith(
        "This is not financial advice."
    )  # added because the reply lacked it
    assert len(answer.chunks) == 5 and answer.latency_ms >= 0


def test_a_figure_that_is_not_in_the_context_is_removed_from_the_answer():
    reply = "Base rate 7.25% p.a. [1]\n- Women: 7.20% p.a. (7.25% minus 0.05%) [1]\n\n"
    answer = Pipeline(FakeRetriever(), [FakeProvider(reply)]).ask("What is the home loan rate?")
    assert "7.20" not in answer.text and "Base rate 7.25% p.a. [1]" in answer.text
    assert "left out" in answer.text and answer.text.endswith("This is not financial advice.")
    assert not answer.refused


def test_answer_with_only_unsupported_figures_becomes_the_not_found_text():
    reply = "- Rate is 9.99% p.a. [1]\n- Fee 1.11% [2]"
    answer = Pipeline(FakeRetriever(), [FakeProvider(reply)]).ask("What is the home loan rate?")
    assert answer.text.startswith(NOT_FOUND) and answer.sources == []


def test_prompt_sent_to_the_llm_contains_question_context_and_date():
    provider = FakeProvider()
    Pipeline(FakeRetriever(), [provider]).ask("What is the home loan rate?")
    system, user = provider.prompts[0]
    assert system == SYSTEM_PROMPT
    assert "Question: What is the home loan rate?" in user
    assert "[1] Product: P1" in user and "[5] Product: P5" in user
    assert "2026-10-28" in user


def test_failing_provider_falls_back_to_the_next_one():
    bad, good = FakeProvider(fail=True), FakeProvider("Fine [1].")
    good.name = "second/model"
    answer = Pipeline(FakeRetriever(), [bad, good]).ask("home loan rate")
    assert answer.provider == "second/model" and "Fine" in answer.text


def test_all_providers_failing_raises_a_clear_error():
    with pytest.raises(ProviderError, match="all providers failed"):
        complete_with_fallback([FakeProvider(fail=True)], "s", "u")
    with pytest.raises(ProviderError, match="no LLM key"):
        complete_with_fallback([], "s", "u")


def test_line_references_after_a_citation_number_are_dropped():
    raw = "Up to 5 lakh[1†L4-L5] over 60 months[1†L5][2†line11]."
    assert clean_model_text(raw) == "Up to 5 lakh[1] over 60 months[1][2]."
    assert cited_numbers(clean_model_text(raw)) == [1, 2]
