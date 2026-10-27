"""Build and load the FAISS index (exact inner-product search over unit vectors = cosine)."""

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import faiss

from loan_assistant.config import settings
from loan_assistant.processing.chunk import chunks_path
from loan_assistant.rag.embed import embed_passages

log = logging.getLogger(__name__)

INDEX_FILE = "faiss.index"
META_FILE = "chunks_meta.json"


def load_chunks(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or chunks_path()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build_index(chunks: list[dict[str, Any]], model_name: str | None = None) -> faiss.Index:
    vectors = embed_passages([c["text"] for c in chunks], model_name)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index


def save_index(
    index: faiss.Index, chunks: list[dict[str, Any]], out_dir: Path | None = None
) -> None:
    out_dir = out_dir or settings.index_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(out_dir / INDEX_FILE))
    meta = {
        "embedding_model": settings.embedding_model,
        "dim": index.d,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "chunks": chunks,
    }
    (out_dir / META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )


def load_index(index_dir: Path | None = None) -> tuple[faiss.Index, list[dict[str, Any]]]:
    index_dir = index_dir or settings.index_dir
    index = faiss.read_index(str(index_dir / INDEX_FILE))
    meta = json.loads((index_dir / META_FILE).read_text(encoding="utf-8"))
    if meta["embedding_model"] != settings.embedding_model:
        raise ValueError(
            f"index was built with {meta['embedding_model']}, settings say "
            f"{settings.embedding_model}; rebuild: python -m loan_assistant.build --step index"
        )
    chunks = meta["chunks"]
    if index.ntotal != len(chunks):
        raise ValueError(f"index has {index.ntotal} vectors but {len(chunks)} chunks")
    return index, chunks


def run_index() -> dict[str, Any]:
    chunks = load_chunks()
    index = build_index(chunks)
    save_index(index, chunks)
    summary = {"chunks": len(chunks), "dim": int(index.d), "vectors": int(index.ntotal)}
    log.info("index: %s", summary)
    return summary


def main() -> None:
    argparse.ArgumentParser(description="Embed the chunks and build the FAISS index.").parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for key, value in run_index().items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
