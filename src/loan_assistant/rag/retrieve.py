"""Hybrid retrieval: FAISS (meaning) + BM25 (exact words), merged with Reciprocal Rank Fusion."""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

from loan_assistant.config import settings
from loan_assistant.rag.embed import embed_query
from loan_assistant.rag.index import load_index

log = logging.getLogger(__name__)

TOKEN = re.compile(r"\d+(?:\.\d+)?|[a-z]+")
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it me my of on or "
    "the their there this to was what when which who will with you your".split()
)


def tokenize(text: str) -> list[str]:
    """Lower-case words and numbers (7.25 stays one token); common function words dropped."""
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


@dataclass
class RetrievedChunk:
    chunk: dict[str, Any]
    score: float  # RRF score in hybrid mode, cosine similarity in vector mode
    vector_score: float  # cosine similarity between question and chunk, always filled
    vector_rank: int | None  # 1-based rank in the FAISS candidates, None if not a candidate
    bm25_rank: int | None  # 1-based rank in the BM25 candidates, None if not a candidate


def rrf_fuse(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion: each list gives ``1 / (k + rank)`` to the ids it ranks."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


class Retriever:
    def __init__(self, index_dir: Path | None = None) -> None:
        self.index, self.chunks = load_index(index_dir)
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in self.chunks])

    def search(
        self, question: str, mode: str | None = None, top_k: int | None = None
    ) -> list[RetrievedChunk]:
        mode = mode or settings.retrieval_mode
        if mode not in ("vector", "hybrid"):
            raise ValueError(f"retrieval mode must be 'vector' or 'hybrid', not {mode!r}")
        top_k = top_k or settings.top_k
        n_candidates = settings.candidates_k

        # Cosine similarity to every chunk (415 vectors: exact search costs nothing).
        sims, ids = self.index.search(embed_query(question), self.index.ntotal)
        cosine = np.zeros(self.index.ntotal, dtype=np.float32)
        cosine[ids[0]] = sims[0]
        vector_ranking = [int(i) for i in ids[0][:n_candidates]]
        vector_rank = {i: r for r, i in enumerate(vector_ranking, start=1)}

        if mode == "vector":
            return [
                RetrievedChunk(self.chunks[i], float(cosine[i]), float(cosine[i]), r, None)
                for i, r in list(vector_rank.items())[:top_k]
            ]

        bm25_scores = self.bm25.get_scores(tokenize(question))
        bm25_order = [int(i) for i in np.argsort(-bm25_scores)[:n_candidates] if bm25_scores[i] > 0]
        bm25_rank = {i: r for r, i in enumerate(bm25_order, start=1)}

        fused = rrf_fuse([vector_ranking, bm25_order], k=settings.rrf_k)
        # Ties (same RRF score) go to the chunk that is closer in meaning.
        best = sorted(fused, key=lambda i: (-fused[i], -cosine[i]))[:top_k]
        return [
            RetrievedChunk(
                self.chunks[i],
                fused[i],
                float(cosine[i]),
                vector_rank.get(i),
                bm25_rank.get(i),
            )
            for i in best
        ]


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    return Retriever()
