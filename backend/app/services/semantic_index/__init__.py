from __future__ import annotations

import threading
from typing import Any

from app.config.settings import settings

_embedding_model = None
_embedding_lock = threading.Lock()

FAISS_INDEX_PATH = settings.FAISS_INDEX_PATH
FAISS_MAPPING_PATH = settings.FAISS_MAPPING_PATH


def get_sentence_embedding_model() -> Any:
    global _embedding_model
    if _embedding_model is None:
        with _embedding_lock:
            if _embedding_model is None:
                from sentence_transformers import SentenceTransformer  # lazy import
                _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model


def generate_embedding(text: str):
    model = get_sentence_embedding_model()
    return model.encode([text])[0]