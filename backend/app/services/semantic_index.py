"""Semantic indexing helpers: sentence-transformers embeddings + FAISS + ID mapping.

"""

from __future__ import annotations

import os
import pickle
import threading
from typing import Dict

from ..config.settings import settings

_embedding_model = None
_embedding_lock = threading.Lock()

FAISS_INDEX_PATH = settings.FAISS_INDEX_PATH
FAISS_MAPPING_PATH = settings.FAISS_MAPPING_PATH


def get_sentence_embedding_model():
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


def load_faiss_index(dimension: int):
    import faiss  # type: ignore
    if os.path.exists(FAISS_INDEX_PATH):
        return faiss.read_index(FAISS_INDEX_PATH)
    index = faiss.IndexFlatL2(dimension)
    return index


def save_faiss_index(index) -> None:
    import faiss  # type: ignore
    os.makedirs(os.path.dirname(FAISS_INDEX_PATH), exist_ok=True)
    faiss.write_index(index, FAISS_INDEX_PATH)


def load_faiss_mapping() -> Dict[int, int]:
    if os.path.exists(FAISS_MAPPING_PATH):
        with open(FAISS_MAPPING_PATH, "rb") as f:
            return pickle.load(f)
    return {}


def save_faiss_mapping(mapping: Dict[int, int]) -> None:
    os.makedirs(os.path.dirname(FAISS_MAPPING_PATH), exist_ok=True)
    with open(FAISS_MAPPING_PATH, "wb") as f:
        pickle.dump(mapping, f)
