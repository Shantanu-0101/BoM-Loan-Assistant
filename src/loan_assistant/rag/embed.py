"""Local sentence embeddings (FastEmbed, ONNX, CPU only)."""

import logging
from collections.abc import Sequence
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding

from loan_assistant.config import settings

log = logging.getLogger(__name__)

BATCH_SIZE = 32


@lru_cache(maxsize=1)
def _model(name: str) -> TextEmbedding:
    log.info("loading embedding model %s", name)
    return TextEmbedding(model_name=name)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


def embed_passages(texts: Sequence[str], model_name: str | None = None) -> np.ndarray:
    """Embed document chunks; returns a float32 matrix of unit-length rows."""
    model = _model(model_name or settings.embedding_model)
    vectors = np.array(list(model.embed(list(texts), batch_size=BATCH_SIZE)), dtype=np.float32)
    return _normalize(vectors)


def embed_query(question: str, model_name: str | None = None) -> np.ndarray:
    """Embed one question with the model's query mode; returns shape (1, dim), unit length."""
    model = _model(model_name or settings.embedding_model)
    vectors = np.array(list(model.query_embed([question])), dtype=np.float32)
    return _normalize(vectors)
