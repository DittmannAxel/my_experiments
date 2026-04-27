"""Embedding model wrapper. mxbai-embed-large-v1 → 1024-dim vectors."""
from __future__ import annotations

import logging
import threading

from sentence_transformers import SentenceTransformer

log = logging.getLogger("embedder")

MODEL_NAME = "mixedbread-ai/mxbai-embed-large-v1"

_lock = threading.Lock()
_model: SentenceTransformer | None = None


def _ensure_loaded() -> SentenceTransformer:
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                log.info("Loading embedding model %s ...", MODEL_NAME)
                _model = SentenceTransformer(MODEL_NAME, device="cpu")
                log.info("Embedding model ready (dim=%d)", _model.get_sentence_embedding_dimension())
    return _model


def embed(text: str) -> list[float]:
    m = _ensure_loaded()
    v = m.encode(text, normalize_embeddings=True, convert_to_numpy=True)
    return v.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    m = _ensure_loaded()
    arr = m.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        batch_size=16,
        show_progress_bar=False,
    )
    return [v.tolist() for v in arr]
