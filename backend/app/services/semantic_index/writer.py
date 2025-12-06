from __future__ import annotations

import os
import pickle
from typing import Iterable, List

import numpy as np  # type: ignore

from . import FAISS_INDEX_PATH, FAISS_MAPPING_PATH

_index = None
_faiss = None
_mapping = None  # Dict[int, int]


def _get_faiss():
    global _faiss
    if _faiss is None:
        import faiss  # type: ignore
        _faiss = faiss
    return _faiss


def _load_mapping() -> dict[int, int]:
    global _mapping
    if _mapping is not None:
        return _mapping
    if os.path.exists(FAISS_MAPPING_PATH):
        with open(FAISS_MAPPING_PATH, "rb") as f:
            _mapping = pickle.load(f)
    else:
        _mapping = {}
    return _mapping


def _save_mapping(mapping: dict[int, int]) -> None:
    os.makedirs(os.path.dirname(FAISS_MAPPING_PATH), exist_ok=True)
    with open(FAISS_MAPPING_PATH, "wb") as f:
        pickle.dump(mapping, f)


def load_or_create_index(dim: int):
    """
    Load FAISS index from disk if present, otherwise create a new empty one.
    """
    global _index
    faiss = _get_faiss()
    if _index is not None:
        return _index

    if os.path.exists(FAISS_INDEX_PATH):
        _index = faiss.read_index(FAISS_INDEX_PATH)
    else:
        _index = faiss.IndexFlatL2(dim)
    return _index


def add_embeddings(vectors: np.ndarray | List[List[float]], mapping_ids: Iterable[int]) -> None:
    """
    Add embeddings to the index and update the on-disk mapping file.

    vectors: numpy array (n, d) or list of lists
    mapping_ids: iterable of video_ids corresponding to each vector
    """
    global _index
    vecs = np.array(vectors, dtype="float32")
    if vecs.ndim != 2:
        raise ValueError("vectors must be 2D array of shape (n, d)")
    n = vecs.shape[0]
    if n == 0:
        return

    index = load_or_create_index(vecs.shape[1])
    before = getattr(index, "ntotal", 0)
    index.add(vecs)
    after = getattr(index, "ntotal", before + n)

    # Update mapping
    mapping = _load_mapping()
    start_id = after - n
    for offset, vid in enumerate(mapping_ids):
        mapping[start_id + offset] = int(vid)
    _save_mapping(mapping)


def save_index() -> None:
    """
    Persist the index to disk.
    """
    faiss = _get_faiss()
    index = _index
    if index is None:
        return
    os.makedirs(os.path.dirname(FAISS_INDEX_PATH), exist_ok=True)
    faiss.write_index(index, FAISS_INDEX_PATH)
