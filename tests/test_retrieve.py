import pytest

from loan_assistant.config import settings
from loan_assistant.rag.retrieve import Retriever, rrf_fuse, tokenize

HOME = "https://bankofmaharashtra.bank.in/personal-banking/loans/home-loan"
SALARIED = "https://bankofmaharashtra.bank.in/personal-loan-for-salaried-customers"
FLEXI = "https://bankofmaharashtra.bank.in/maha-super-flexi-housing-loan-scheme"

# The four example questions and the page that must be in the top 5.
PDF_QUESTIONS = [
    ("What are the interest rates for a Bank of Maharashtra home loan?", HOME),
    (
        "What is the maximum tenure for a personal loan if my salary account is with the bank?",
        SALARIED,
    ),
    ("Tell me about the Maha Super Flexi Housing Loan Scheme.", FLEXI),
    (
        "Are there any processing fee concessions for women or defence personnel on home loans?",
        HOME,
    ),
]


def test_rrf_rewards_agreement_between_lists():
    fused = rrf_fuse([[1, 2, 3], [3, 1, 4]], k=60)
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[3] == pytest.approx(1 / 63 + 1 / 61)
    assert sorted(fused, key=lambda i: -fused[i])[:2] == [1, 3]  # both lists rank 1 and 3 high
    assert fused[2] < fused[1] and fused[4] < fused[1]


def test_rrf_with_one_list_keeps_its_order():
    fused = rrf_fuse([[7, 5, 9]])
    assert sorted(fused, key=lambda i: -fused[i]) == [7, 5, 9]


def test_tokenize_keeps_decimals_and_drops_function_words():
    assert tokenize("What is the rate, 7.25% p.a. for Rs.50 lakh?") == [
        "rate",
        "7.25",
        "p",
        "rs",
        "50",
        "lakh",
    ]


@pytest.fixture(scope="module")
def retriever() -> Retriever:
    if not (settings.index_dir / "faiss.index").exists():
        pytest.skip("index not built: python -m loan_assistant.build --step index")
    return Retriever()


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
@pytest.mark.parametrize(("question", "expected_url"), PDF_QUESTIONS)
def test_pdf_question_finds_the_right_page_in_top_5(retriever, mode, question, expected_url):
    results = retriever.search(question, mode=mode)
    assert len(results) == settings.top_k
    assert expected_url in [r.chunk["url"] for r in results]


def test_scores_are_sorted_and_hybrid_reports_both_ranks(retriever):
    results = retriever.search(PDF_QUESTIONS[2][0], mode="hybrid")
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert any(r.bm25_rank is not None and r.vector_rank is not None for r in results)
    assert all(-1.0 <= r.vector_score <= 1.0 for r in results)


def test_vector_mode_scores_are_cosine_similarities(retriever):
    results = retriever.search("home loan interest rate", mode="vector")
    assert all(r.score == r.vector_score and r.bm25_rank is None for r in results)


def test_unknown_mode_is_rejected(retriever):
    with pytest.raises(ValueError):
        retriever.search("anything", mode="semantic")
