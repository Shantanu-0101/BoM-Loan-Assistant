"""Bank of Maharashtra Loan Assistant: the chat app.

HOW TO RUN (from the project root, with your virtual environment active):

    streamlit run deliverables/streamlit_app.py

WHAT THIS FILE IS
    Only the user interface. It shows the chat, the sources and the settings. All the real work
    (search, prompt, language model, number check) happens in one call: ``pipeline.ask(question)``.
    The notebook walkthrough explains each step of that call, part by part.

HOW THE FILE IS ORGANISED
    Section 1: Imports and project path
    Section 2: Fixed text and page settings
    Section 3: Load the pipeline once
    Section 4: Draw one chat message
    Section 5: The sidebar
    Section 6: The main page

ONE THING TO KNOW ABOUT STREAMLIT
    Streamlit runs this whole file from top to bottom again after every click or message. Anything
    that must survive between runs goes in ``st.session_state`` (a dictionary Streamlit keeps for
    each visitor), and anything slow to load goes behind ``@st.cache_resource``.
"""

# ==================================================================================================
# Section 1: Imports and project path
# ==================================================================================================
import sys
from pathlib import Path

import streamlit as st

# The app lives in deliverables/, and the project's code lives in src/. This line tells Python where
# to find it, so a fresh clone can run the app without "pip install -e .".
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from loan_assistant.llm.providers import ProviderError  # noqa: E402  (Part 10 of the notebook)
from loan_assistant.rag.generate import link_citations, sources_by_page  # noqa: E402  (Part 12)
from loan_assistant.rag.pipeline import Answer, Pipeline  # noqa: E402  (Part 12)

# ==================================================================================================
# Section 2: Fixed text and page settings
# ==================================================================================================
OFFICIAL_SITE = "https://bankofmaharashtra.bank.in"

# Shown as buttons on an empty chat, so a new user does not face a blank page.
EXAMPLE_QUESTIONS = [
    "What are the interest rates for a Bank of Maharashtra home loan?",
    "What is the maximum tenure for a personal loan if my salary account is with the bank?",
    "Tell me about the Maha Super Flexi Housing Loan Scheme.",
    "Are there any processing fee concessions for women or defence personnel on home loans?",
]

# A plain-language summary of the pipeline, shown in the sidebar.
HOW_IT_WORKS = """
1. **Check** that the question is not about another bank.
2. **Search** the bank's pages by meaning and by exact words.
3. **Merge** both result lists and keep the best 5 passages.
4. **Refuse** if nothing is similar enough to the question.
5. **Ask** the language model to answer from those passages only.
6. **Check the numbers:** remove any figure that is not in the passages.
"""

# This must be the first Streamlit command in the file.
st.set_page_config(page_title="BoM Loan Assistant", page_icon="🏦", layout="centered")


# ==================================================================================================
# Section 3: Load the pipeline once
# ==================================================================================================
@st.cache_resource(show_spinner="Loading the loan knowledge base...")
def get_pipeline() -> Pipeline:
    """Load the search index and the model clients.

    Loading takes a few seconds. @st.cache_resource makes it happen once for the whole app, instead
    of on every click.
    """
    return Pipeline()


# ==================================================================================================
# Section 4: Draw one chat message
# ==================================================================================================
def render_sources(answer: Answer) -> None:
    """Show the pages the answer cites. A reader can click through and check the claim."""
    if not answer.sources:
        return
    pages = sources_by_page(answer.sources)  # one entry per page: (url, title, [citation numbers])
    with st.expander(f"Sources ({len(pages)})"):
        for url, title, numbers in pages:
            label = "".join(f"[{n}]" for n in numbers)
            st.markdown(f"**{label}** [{title}]({url})")


def render_retrieved_chunks(answer: Answer) -> None:
    """Show exactly what the language model was given, with the scores that chose each passage."""
    if not answer.chunks:
        return
    with st.expander(f"Retrieved chunks ({len(answer.chunks)})"):
        for number, retrieved in enumerate(answer.chunks, start=1):
            chunk = retrieved.chunk
            st.markdown(
                f"**[{number}] {chunk['product']}** — {chunk['section']}  \n"
                f"similarity {retrieved.vector_score:.3f} · "
                f"vector rank {retrieved.vector_rank or '-'} · "
                f"keyword rank {retrieved.bm25_rank or '-'} · "
                f"[page]({chunk['url']})"
            )
            st.text(chunk["text"])


def render_message(message: dict, show_chunks: bool) -> None:
    """Draw one message. A user message is plain text, and an assistant message has details."""
    with st.chat_message(message["role"]):
        answer: Answer | None = message.get("answer")

        # A user's question, or an error message: just show the text.
        if answer is None:
            st.markdown(message["content"])
            return

        # A refusal looks different from an answer, so nobody mistakes one for the other.
        if answer.refused:
            st.info(answer.text)
        else:
            # Turns each [n] into a link to its source page.
            st.markdown(link_citations(answer.text, answer.sources))

        render_sources(answer)
        if show_chunks:
            render_retrieved_chunks(answer)

        # One small line that says how this answer was produced.
        status = (
            f"refused ({answer.reason})" if answer.refused else f"answered by {answer.provider}"
        )
        st.caption(f"{status} · {answer.latency_ms / 1000:.1f} s")


# ==================================================================================================
# Section 5: The sidebar
# ==================================================================================================
def render_sidebar() -> tuple[str, bool]:
    """Draw the settings and return what the user chose: (search mode, show chunks or not)."""
    with st.sidebar:
        st.header("Settings")

        # Students can switch modes and compare the same question both ways.
        mode = st.radio(
            "Search method",
            ["hybrid", "vector"],
            help="Hybrid combines meaning-based and keyword search. Vector uses meaning only.",
        )
        # Opening the "black box": see which passages the model was given.
        show_chunks = st.toggle("Show retrieved chunks", value=False)

        if st.button("Clear conversation"):
            st.session_state.messages = []
            st.rerun()  # draw the page again straight away, now empty

        st.divider()
        with st.expander("How an answer is made"):
            st.markdown(HOW_IT_WORKS)

        st.caption(
            "Not financial advice. Rates change; confirm at "
            f"[bankofmaharashtra.bank.in]({OFFICIAL_SITE}) or a branch."
        )
    return mode, show_chunks


# ==================================================================================================
# Section 6: The main page
# ==================================================================================================
def main() -> None:
    st.title("Bank of Maharashtra loan assistant")
    st.caption(
        "Ask about home, personal, vehicle, education, gold, MSME and agriculture loans. "
        "Answers come only from the bank's official website, with sources."
    )

    mode, show_chunks = render_sidebar()

    # Step 1: load the pipeline. If the index is missing, tell the user what to run.
    try:
        pipeline = get_pipeline()
    except Exception as exc:
        st.error(f"Could not load the knowledge base: {exc}")
        st.code("python -m loan_assistant.build --all", language="bash")
        st.stop()

    # Step 2: remember the conversation, and draw everything said so far.
    if "messages" not in st.session_state:
        st.session_state.messages = []
    for message in st.session_state.messages:
        render_message(message, show_chunks)

    # Step 3: on an empty chat, offer example questions.
    if not st.session_state.messages:
        st.markdown("**Try one of these:**")
        for i, example in enumerate(EXAMPLE_QUESTIONS):
            if st.button(example, key=f"example-{i}", width="stretch"):
                st.session_state.pending = example  # picked up on the next run, in step 4
                st.rerun()

    # Step 4: get a question, either typed in the box or from an example button.
    typed_question = st.chat_input("Ask about a Bank of Maharashtra loan...")
    question = st.session_state.pop("pending", None) or typed_question

    # Step 5: answer it. This one call to pipeline.ask() is the whole RAG system.
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        render_message(st.session_state.messages[-1], show_chunks)

        with st.chat_message("assistant"), st.spinner("Searching the bank's pages..."):
            try:
                answer = pipeline.ask(question, mode=mode)
                reply = {"role": "assistant", "content": "", "answer": answer}
            except ProviderError as exc:  # no API key, or every model failed
                error = f"The language model could not answer right now. {exc}"
                reply = {"role": "assistant", "content": error}

        st.session_state.messages.append(reply)
        st.rerun()  # draw the page again, so the answer appears through step 2

    # Step 6: say how old the information is.
    answers = [m["answer"] for m in st.session_state.messages if m.get("answer")]
    data_date = answers[-1].data_as_of if answers else pipeline.knowledge_base_date()
    st.divider()
    st.caption(f"Data as of {data_date.strftime('%d %b %Y')} · official site only")


main()
