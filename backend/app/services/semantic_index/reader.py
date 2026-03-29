from __future__ import annotations

import logging
import os
import time
from typing import Tuple

import numpy as np  # type: ignore

from . import FAISS_INDEX_PATH

_index = None  # cached FAISS index
_faiss = None  # lazy import holder
logger = logging.getLogger(__name__)


def _get_faiss():
    global _faiss
    if _faiss is None:
        import faiss  # type: ignore
        _faiss = faiss
    return _faiss


def _move_unreadable_file_aside(path: str) -> None:
    if not os.path.exists(path):
        return

    timestamp = int(time.time())
    backup_path = f"{path}.unreadable.{timestamp}"
    candidate = backup_path
    suffix = 1
    while os.path.exists(candidate):
        candidate = f"{backup_path}.{suffix}"
        suffix += 1

    try:
        os.replace(path, candidate)
        logger.warning("Moved unreadable FAISS index from %s to %s", path, candidate)
    except OSError as exc:
        logger.warning("Failed to move unreadable FAISS index at %s aside: %s", path, exc)


def load_index_if_exists(dim: int):
    """
    Load an existing FAISS index from disk if present.
    Otherwise, create an in-memory flat L2 index for read-only searches.

    Returns an index object intended for search operations only.
    """
    global _index
    faiss = _get_faiss()
    if _index is not None:
        return _index

    if os.path.exists(FAISS_INDEX_PATH):
        try:
            _index = faiss.read_index(FAISS_INDEX_PATH)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to load FAISS index from %s: %s. Returning an empty in-memory index.",
                FAISS_INDEX_PATH,
                exc,
            )
            _move_unreadable_file_aside(FAISS_INDEX_PATH)
            _index = faiss.IndexFlatL2(dim)
    else:
        # empty in-memory flat index (read/search only usage here)
        _index = faiss.IndexFlatL2(dim)
    return _index


def search_vector(vec: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Perform a search on the underlying index.

    Returns:
      distances: shape (k,)
      indices: shape (k,)
    """
    if vec.dtype != np.float32:
        vec = vec.astype("float32")
    dim = int(vec.shape[-1])
    index = load_index_if_exists(dim)

    if hasattr(index, "nprobe"):
        index.nprobe = max(8, min(64, k * 4))

    k = min(k, getattr(index, "ntotal", 0)) if getattr(index, "ntotal", 0) > 0 else k
    D, I = index.search(vec.reshape(1, -1), k)
    return D[0], I[0]
