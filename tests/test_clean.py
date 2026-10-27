from pathlib import Path

import pytest

from loan_assistant.processing.clean import (
    clean_html,
    clean_pdf_text,
    normalize_text,
)

FIXTURES = Path(__file__).parent / "fixtures"
CHROME = [
    "MENU_ITEM",
    "SECRET_SCRIPT",
    "FOOTER_TEXT",
    "COOKIE_BANNER",
    "FLOAT_WIDGET",
    "SIMILAR_CARD",
]


@pytest.fixture(scope="module")
def tabbed():
    return clean_html((FIXTURES / "sample_tabbed_page.html").read_bytes(), "https://x/sample")


@pytest.fixture(scope="module")
def article():
    return clean_html((FIXTURES / "sample_article_page.html").read_bytes(), "https://x/flexi")


def md(page) -> str:
    return page.to_markdown()


def test_chrome_is_removed(tabbed, article):
    for page in (tabbed, article):
        text = md(page)
        for junk in CHROME:
            assert junk not in text
        assert "<" not in text and "&nbsp;" not in text and "&amp;" not in text
        for widget in ("Get a Call Back", "Apply Online", "Calculate EMI", "Skip to main"):
            assert widget not in text


def test_tabbed_sections_follow_the_tabs_in_order(tabbed):
    assert tabbed.title == "Sample Housing Loan Scheme"
    assert [s.heading for s in tabbed.sections] == [
        "Overview",
        "Features & Benefits",
        "At a Glance",
        "FAQs",
    ]  # the EMI calculator tab is gone


def test_rate_tile_becomes_one_normalized_line(tabbed):
    assert "Interest Rate: 7.25% p.a.*" in tabbed.sections[0].body


def test_feature_cards_keep_title_and_text_together(tabbed):
    body = tabbed.sections[1].body
    assert "- Low EMI: Flexible repayment options" in body
    assert "Features and benefits" not in body  # the repeated tab sub-heading is dropped


def test_glance_table_is_markdown_with_normalized_values(tabbed):
    body = tabbed.sections[2].body
    assert body.splitlines()[0] == "| Information | Details |"
    assert "| Interest Rate | 7.25% p.a.* |" in body
    assert "| Loan amount | Rs. 50,00,000 or Rs. 75 Lakh |" in body
    assert "| Women concession | 0.05% |" in body


def test_faq_questions_are_kept_as_headings(tabbed):
    faq = tabbed.sections[3].body
    assert "### Can the loan be transferred?" in faq
    assert "Yes, a home loan can be transferred from another bank." in faq


def test_unclosed_heading_wrapper_does_not_swallow_the_article(article):
    assert (
        article.title == "Sample Flexi Housing Loan Scheme (Term Loan linked with Saving Account.)"
    )
    assert article.words > 60


def test_article_without_h2_splits_on_bold_phrases_and_table_captions(article):
    assert [s.heading for s in article.sections][:3] == [
        "Overview",
        "Key Features",
        "Sample Flexi Housing Loan - for new or existing house",
    ]
    assert "borrower's account" in article.sections[0].body  # curly quote normalized


def test_table_is_complete_and_nested_table_is_flattened_into_its_cell(article):
    table = next(s.body for s in article.sections if s.body.startswith("| S.No"))
    lines = table.splitlines()
    assert lines[0] == "| S.No | Particulars | Scheme guidelines |"
    assert lines[1] == "|---|---|---|"
    assert "| 1 | Loan Amount | Minimum - Rs. 50.00 Lakh; Maximum - No Maximum Limit |" in lines
    assert (
        "| 2 | Loan to value | Loan amount - LTV; Up to Rs. 75.00 Lakh - 80%; "
        "Above Rs. 75.00 Lakh - 75% |" in lines
    )
    assert "| 3 | Purpose | Purchase of flat. Takeover of loans |" in lines
    assert all(line.count("|") == 4 for line in lines)  # no row lost a column


def test_table_inside_a_list_item_stays_a_real_table(article):
    body = next(s.body for s in article.sections if s.heading == "Interest Rate")
    assert "1. Housing loan rates by score:" in body
    rows = body.splitlines()
    assert "| CIBIL | Rate |" in rows  # table rows start at column 0, not indented
    assert "| 800 & above | 7.25% |" in rows
    assert "  - Concession for women" in body
    assert "2. Second item" in body


def test_content_links_survive_but_apply_buttons_do_not(article):
    body = next(s.body for s in article.sections if s.heading == "Interest Rate")
    assert "See the Interest Rate page." in body


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Rs.50,000/-", "Rs. 50,000/-"),
        ("INR 5 lakh", "Rs. 5 lakh"),
        ("₹ 75,000", "Rs. 75,000"),
        ("rs 10", "Rs. 10"),
        ("7.25 %P.A*", "7.25% p.a.*"),
        ("7.25%p.a.", "7.25% p.a."),
        ("8.5 per cent", "8.5%"),
        ("a b​c  d", "a bc d"),
        ("it’s “ok” – fine", 'it\'s "ok" - fine'),
        ("Interest Rate : 7%", "Interest Rate: 7%"),
        ("Rs. 5 lakhs", "Rs. 5 lakhs"),
        ("Rsvp by Friday", "Rsvp by Friday"),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_pdf_text_rejoins_lines_and_drops_the_stamp():
    page = clean_pdf_text(
        "Restricted\nInterest Rate range\nfor the quarter\n\nHousing\n| a | b |\n"
    )
    assert page.title == "Interest Rate range for the quarter"
    body = page.sections[0].body
    assert "Restricted" not in body
    assert "| a | b |" in body
    assert not body.startswith("Interest Rate range")  # title is not repeated


REAL_HOME = (
    Path(__file__).parents[1] / "data" / "raw" / "html" / "personal-banking-loans-home-loan.html"
)


@pytest.mark.skipif(not REAL_HOME.exists(), reason="raw HTML is not committed")
def test_real_home_loan_page_has_every_tab_and_the_faq_questions():
    page = clean_html(REAL_HOME.read_bytes(), "https://bankofmaharashtra.bank.in/home")
    assert [s.heading for s in page.sections] == [
        "Overview",
        "Features & Benefits",
        "Home Loan at a Glance",
        "Documents Required",
        "Interest Rates",
        "Check Eligibility",
        "FAQs",
        "How to Apply",
    ]
    faq = next(s for s in page.sections if s.heading == "FAQs").body
    assert faq.count("### ") >= 8
    assert "Interest Rate: 7.25% p.a.*" in page.sections[0].body


def test_know_more_is_removed_only_as_a_trailing_link_label():
    html = (
        "<div class='maincontent'><h1>T</h1><ul>"
        "<li>Scheme for repairs <a href='/x'>Know More...</a></li>"
        "<li>To know more about charges, see below</li></ul></div>"
    )
    body = clean_html(html).sections[0].body
    assert "- Scheme for repairs" in body and "Know" not in body.splitlines()[0]
    assert "- To know more about charges, see below" in body


RATES_PDF = (
    Path(__file__).parents[1]
    / "data"
    / "raw"
    / "pdf"
    / "writereaddata-documentlibrary-35e7b238-428f-4e75-abfe-553af9479e59-pdf.pdf"
)


@pytest.mark.skipif(not RATES_PDF.exists(), reason="raw PDFs are not committed")
def test_pdf_table_rows_stay_on_one_line():
    from loan_assistant.processing.pdf_text import pdf_to_text

    text = pdf_to_text(RATES_PDF)
    assert "| Housing | 7.05 | 9.55 | 7.35 |" in text
    assert "| Personal | 7.25 | 13.55 | 10.24 |" in text
