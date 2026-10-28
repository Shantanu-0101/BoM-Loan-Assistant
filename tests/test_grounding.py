from decimal import Decimal

from loan_assistant.rag.grounding import NOTE, ground_answer, numbers_in

CONTEXT = [
    "Product: Home Loan | Section: Interest | Source: https://bankofmaharashtra.bank.in/x\n\n"
    "Housing Loan Base Rate 7.25% p.a. Women borrowers: concession of 0.05%. "
    "Processing fee 0.25% of loan, maximum Rs. 25,000. Tenure up to 30 years.",
    "Salary account with the bank: 84 months. Others: 60 months. Loan from Rs. 2,00,000.",
]
DISCLAIMER = "Information as of 2026-10-28; rates may change. This is not financial advice."


def test_equal_numbers_match_whatever_their_spelling():
    assert numbers_in("7.2 and 7.20 and 2,00,000") == {Decimal("7.2"), Decimal("200000")}


def test_answer_whose_figures_are_all_in_the_context_is_unchanged():
    answer = (
        "**Rate:** 7.25% p.a.[1]\n- Women: 0.05% concession[1]\n- Fee 0.25%, max Rs. 25,000[1]\n\n"
        + DISCLAIMER
    )
    result = ground_answer(answer, CONTEXT, "home loan rate?")
    assert result.text == answer and result.removed == [] and not result.emptied


def test_a_figure_made_by_arithmetic_is_removed_with_its_line():
    answer = (
        "Standard rate: 7.25% p.a.[1]\n"
        "- Women borrowers: **7.20% p.a.** (7.25% - 0.05% concession)[1]\n"
        "- Women get a 0.05% concession[1]\n\n" + DISCLAIMER
    )
    result = ground_answer(answer, CONTEXT)
    assert result.removed == ["7.20"]
    assert "7.20" not in result.text
    assert "Standard rate: 7.25% p.a.[1]" in result.text
    assert "Women get a 0.05% concession[1]" in result.text
    assert NOTE in result.text
    assert result.text.endswith(DISCLAIMER)  # the disclaimer stays the last line
    assert result.text.index(NOTE) < result.text.index(DISCLAIMER)
    assert not result.emptied


def test_only_the_bad_sentence_goes_when_a_line_has_several():
    answer = "The base rate is 7.25% p.a.[1] Women pay 7.20% p.a.[1]\n\n" + DISCLAIMER
    result = ground_answer(answer, CONTEXT)
    assert "The base rate is 7.25% p.a.[1]" in result.text
    assert "7.20" not in result.text


def test_numbers_from_the_question_count_as_known():
    answer = "For a CIBIL score of 750 the rate is 7.25%[1]\n\n" + DISCLAIMER
    assert ground_answer(answer, CONTEXT, "rate for CIBIL 750?").removed == []
    assert ground_answer(answer, CONTEXT, "rate for good CIBIL?").removed == ["750"]


def test_citations_list_numbers_urls_dates_and_small_counts_are_not_checked():
    answer = (
        "1. First point about 7.25%[1][2]\n"
        "2. See https://bankofmaharashtra.bank.in/page-9876 for more[1, 2]\n"
        "There are 4 schemes and the form is on page 3[1]\n\n" + DISCLAIMER
    )
    assert ground_answer(answer, CONTEXT).removed == []


def test_a_small_whole_number_with_a_unit_is_checked():
    answer = "Tenure is up to 7 years[1]\n\n" + DISCLAIMER
    result = ground_answer(answer, CONTEXT)
    assert result.removed == ["7"]


def test_table_rows_are_removed_whole():
    answer = "| Rate | 7.25% |\n| Rate for women | 7.20% |\n\n" + DISCLAIMER
    result = ground_answer(answer, CONTEXT)
    assert "| Rate | 7.25% |" in result.text and "7.20" not in result.text


def test_answer_is_marked_empty_when_no_cited_claim_survives():
    answer = "- Rate is 9.99% p.a.[1]\n- Fee is 1.11%[2]\n\n" + DISCLAIMER
    result = ground_answer(answer, CONTEXT)
    assert result.removed == ["9.99", "1.11"]
    assert result.emptied


def test_a_refusal_without_figures_is_left_alone():
    answer = "I couldn't find that in the information found.\n\n" + DISCLAIMER
    result = ground_answer(answer, CONTEXT)
    assert result.text == answer and not result.emptied


def test_a_date_written_with_dots_in_the_context_matches_the_same_date_with_dashes():
    context = ["Processing fee is waived up to 31.12.2025 for all borrowers."]
    answer = "The fee waiver applies up to 31-12-2025[1]\n\n" + DISCLAIMER
    assert ground_answer(answer, context).removed == []


def test_zero_percent_is_not_checked_because_the_bank_writes_zero():
    answer = "The Flexi account earns 0 % interest[1]\n\n" + DISCLAIMER
    assert ground_answer(answer, ["Flexi account: zero interest"]).removed == []


def test_a_conversion_in_brackets_is_removed_but_the_sentence_stays():
    context = ["Repayment up to 180 months or age 70 years, whichever is earlier."]
    answer = "**Repayment:** up to 180 months (15 years) or age 70[1]\n\n" + DISCLAIMER
    result = ground_answer(answer, context)
    assert result.removed == ["15"]
    assert "**Repayment:** up to 180 months or age 70[1]" in result.text
    assert "15 years" not in result.text and not result.emptied
