import json
from unittest.mock import patch

from app.services import semantic_index


def test_search_empty_index(client):
    resp = client.get('/videos/search?q=test')
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_grouping(client, monkeypatch):
    # Fake embeddings and index behavior
    # We'll monkeypatch generate_embedding and FAISS load to simulate two videos with multiple segments.
    import numpy as np

    class FakeIndex:
        def __init__(self):
            # 4 vectors, distances: order we'll simulate search returning
            self.ntotal = 4
            self._dim = 3
        def search(self, vec, k):
            # Distances correspond to two segments for video 1, two for video 2
            # Lower distance => higher similarity after conversion
            return np.array([[0.1, 0.5, 0.2, 0.6]]), np.array([[0, 1, 2, 3]])

    # Mapping: faiss_id -> video_id (0,1 -> video 10) (2,3 -> video 20)
    fake_mapping = {0: 10, 1: 10, 2: 20, 3: 20}

    monkeypatch.setattr(semantic_index, 'generate_embedding', lambda q: np.array([0.0, 0.0, 0.0]))
    monkeypatch.setattr(semantic_index, 'load_faiss_index', lambda dim: FakeIndex())
    monkeypatch.setattr(semantic_index, 'load_faiss_mapping', lambda: fake_mapping)

    # Need videos in DB matching IDs 10 and 20
    from sqlalchemy.orm import Session
    from app.core.database import SessionLocal
    from app import models

    db: Session = SessionLocal()
    try:
        db.add(models.Video(id=10, title='Vid A', url='videos/a.mp4'))
        db.add(models.Video(id=20, title='Vid B', url='videos/b.mp4'))
        db.commit()
    finally:
        db.close()

    resp = client.get('/videos/search?q=anything&k=5')
    assert resp.status_code == 200
    data = resp.json()
    # Should return one entry per video id with best similarity order (video with distance 0.1 first)
    assert len(data) == 2
    assert data[0]['video_id'] in {10, 20}
    sims = [d['similarity_score'] for d in data]
    assert sims[0] >= sims[1]
