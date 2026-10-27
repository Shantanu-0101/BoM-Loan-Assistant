"""Question in, cited answer out: guardrails -> retrieve -> generate."""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from loan_assistant.config import settings
from loan_assistant.llm.providers import LLMProvider, default_providers
from loan_assistant.rag.generate import Source, generate_answer, sources_for, with_disclaimer
from loan_assistant.rag.prompt import NOT_FOUND
from loan_assistant.rag.retrieve import RetrievedChunk, Retriever, get_retriever

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Other banks: the retriever happily finds home-loan text for "SBI home loan rate", so this
# is caught before retrieval. A question that also names Bank of Maharashtra is let through.
OTHER_BANKS = re.compile(
    r"\b(sbi|state bank|hdfc|icici|axis bank|kotak|pnb|punjab national|canara|bank of baroda|"
    r"union bank|indian bank|idbi|yes bank|indusind|federal bank|bandhan|central bank|"
    r"bank of india|uco bank|indian overseas|iob|citi ?bank|hsbc|standard chartered|"
    r"paytm|bajaj finance|lic housing|tata capital)\b",
    re.I,
)
OWN_BANK = re.compile(r"maharashtra|\bbom\b|mahabank|maha bank|maha ", re.I)


@dataclass
class Answer:
    text: str
    sources: list[Source]
    chunks: list[RetrievedChunk]
    provider: str  # "none" when no LLM was called
    latency_ms: int
    data_as_of: date
    refused: bool = False
    reason: str = ""  # why it was refused: other-bank | low-score | empty
    top_score: float = field(default=0.0)  # best cosine similarity of the retrieved chunks


def other_bank_mentioned(question: str) -> bool:
    return bool(OTHER_BANKS.search(question)) and not OWN_BANK.search(question)


def to_ist_date(timestamp: str) -> date:
    return datetime.fromisoformat(timestamp).astimezone(IST).date()


class Pipeline:
    def __init__(
        self,
        retriever: Retriever | None = None,
        providers: list[LLMProvider] | None = None,
    ) -> None:
        self.retriever = retriever or get_retriever()
        self.providers = default_providers() if providers is None else providers

    def knowledge_base_date(self) -> date:
        """Date of the newest scraped page in the index."""
        return max(to_ist_date(c["scraped_at"]) for c in self.retriever.chunks)

    def _refuse(self, reason: str, t0: float, chunks: list[RetrievedChunk], top: float) -> Answer:
        as_of = self.knowledge_base_date()
        log.info("refused (%s), top cosine %.3f", reason, top)
        return Answer(
            text=with_disclaimer(NOT_FOUND, as_of),
            sources=[],
            chunks=chunks,
            provider="none",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            data_as_of=as_of,
            refused=True,
            reason=reason,
            top_score=top,
        )

    def ask(self, question: str, mode: str | None = None) -> Answer:
        t0 = time.perf_counter()
        question = question.strip()
        if not question:
            return self._refuse("empty", t0, [], 0.0)
        if other_bank_mentioned(question):
            return self._refuse("other-bank", t0, [], 0.0)

        chunks = self.retriever.search(question, mode=mode)
        top = max((rc.vector_score for rc in chunks), default=0.0)
        if top < settings.min_score:
            return self._refuse("low-score", t0, chunks, top)

        as_of = max(to_ist_date(rc.chunk["scraped_at"]) for rc in chunks)
        text, provider = generate_answer(question, chunks, as_of, self.providers)
        return Answer(
            text=text,
            sources=sources_for(text, chunks),
            chunks=chunks,
            provider=provider,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            data_as_of=as_of,
            top_score=top,
        )
