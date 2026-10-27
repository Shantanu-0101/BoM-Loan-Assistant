"""Chat UI: ``streamlit run app/streamlit_app.py``."""

import sys
from pathlib import Path

import streamlit as st

# Lets a fresh clone run the app without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loan_assistant.llm.providers import ProviderError  # noqa: E402
from loan_assistant.rag.generate import link_citations, sources_by_page  # noqa: E402
from loan_assistant.rag.pipeline import Answer, Pipeline  # noqa: E402

EXAMPLES = [
    "What are the interest rates for a Bank of Maharashtra home loan?",
    "What is the maximum tenure for a personal loan if my salary account is with the bank?",
    "Tell me about the Maha Super Flexi Housing Loan Scheme.",
    "Are there any processing fee concessions for women or defence personnel on home loans?",
]
OFFICIAL_SITE = "https://bankofmaharashtra.bank.in"

st.set_page_config(page_title="BoM Loan Assistant", page_icon="🏦", layout="centered")


@st.cache_resource(show_spinner="Loading the loan knowledge base...")
def get_pipeline() -> Pipeline:
    return Pipeline()


def render_details(answer: Answer, show_chunks: bool) -> None:
    """Under an answer: the cited pages, and optionally the chunks the model was shown."""
    if answer.sources:
        with st.expander(f"Sources ({len(sources_by_page(answer.sources))})"):
            for url, title, numbers in sources_by_page(answer.sources):
                label = "".join(f"[{n}]" for n in numbers)
                st.markdown(f"**{label}** [{title}]({url})")
    if show_chunks and answer.chunks:
        with st.expander(f"Retrieved chunks ({len(answer.chunks)})"):
            for n, rc in enumerate(answer.chunks, start=1):
                c = rc.chunk
                st.markdown(
                    f"**[{n}] {c['product']}** — {c['section']}  \n"
                    f"similarity {rc.vector_score:.3f} · vector rank {rc.vector_rank or '-'} · "
                    f"keyword rank {rc.bm25_rank or '-'} · [page]({c['url']})"
                )
                st.text(c["text"])
    status = f"refused ({answer.reason})" if answer.refused else f"answered by {answer.provider}"
    st.caption(f"{status} · {answer.latency_ms / 1000:.1f} s")


def render_message(message: dict, show_chunks: bool) -> None:
    with st.chat_message(message["role"]):
        answer: Answer | None = message.get("answer")
        if answer is None:
            st.markdown(message["content"])
            return
        if answer.refused:
            st.info(answer.text)
        else:
            st.markdown(link_citations(answer.text, answer.sources))
        render_details(answer, show_chunks)


def main() -> None:
    st.title("Bank of Maharashtra loan assistant")
    st.caption(
        "Ask about home, personal, vehicle, education, gold, MSME and agriculture loans. "
        "Answers come only from the bank's official website, with sources."
    )

    with st.sidebar:
        st.header("Settings")
        mode = st.radio(
            "Search method",
            ["hybrid", "vector"],
            help="Hybrid combines meaning-based and keyword search; vector uses meaning only.",
        )
        show_chunks = st.toggle("Show retrieved chunks", value=False)
        if st.button("Clear conversation"):
            st.session_state.messages = []
            st.rerun()
        st.divider()
        st.caption(
            "Not financial advice. Rates change; confirm at "
            f"[bankofmaharashtra.bank.in]({OFFICIAL_SITE}) or a branch."
        )

    try:
        pipeline = get_pipeline()
    except Exception as exc:  # missing index, bad install: tell the user what to run
        st.error(f"Could not load the knowledge base: {exc}")
        st.code("python -m loan_assistant.build --all", language="bash")
        st.stop()

    if "messages" not in st.session_state:
        st.session_state.messages = []
    for message in st.session_state.messages:
        render_message(message, show_chunks)

    if not st.session_state.messages:
        st.markdown("**Try one of these:**")
        for i, example in enumerate(EXAMPLES):
            if st.button(example, key=f"example-{i}", use_container_width=True):
                st.session_state.pending = example
                st.rerun()

    question = st.chat_input("Ask about a Bank of Maharashtra loan...")
    question = st.session_state.pop("pending", None) or question
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        render_message(st.session_state.messages[-1], show_chunks)
        with st.chat_message("assistant"), st.spinner("Searching the bank's pages..."):
            try:
                answer = pipeline.ask(question, mode=mode)
            except ProviderError as exc:
                answer = None
                error = f"The language model could not answer right now. {exc}"
        if answer is None:
            st.session_state.messages.append({"role": "assistant", "content": error})
        else:
            st.session_state.messages.append({"role": "assistant", "content": "", "answer": answer})
        st.rerun()

    last = next((m["answer"] for m in reversed(st.session_state.messages) if m.get("answer")), None)
    as_of = last.data_as_of if last else pipeline.knowledge_base_date()
    st.divider()
    st.caption(f"Data as of {as_of.strftime('%d %b %Y')} · official site only")


main()
