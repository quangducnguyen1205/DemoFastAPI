import io
import pickle
import uuid

import numpy as np

from app import models
from app.core.database import SessionLocal
import app.routers.videos as videos_router


def _insert_video(**overrides):
    """Persist a video record for integration scenarios."""
    defaults = {
        "title": f"Integration Video {uuid.uuid4().hex}",
        "description": "integration test fixture",
        "url": f"videos/{uuid.uuid4().hex}.mp4",
        "path": f"videos/{uuid.uuid4().hex}.mp4",
        "owner_id": None,
        "status": "processed",
    }
    defaults.update(overrides)

    db = SessionLocal()
    try:
        video = models.Video(**defaults)
        db.add(video)
        db.commit()
        db.refresh(video)
        return video
    finally:
        db.close()


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "message" in data
    assert "docs" in data


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_user_flow_create_and_get(client):
    unique = uuid.uuid4().hex
    payload = {"name": f"Test User {unique}", "email": f"user-{unique}@example.com"}

    create_resp = client.post("/users/", json=payload)
    assert create_resp.status_code == 200, create_resp.text
    user = create_resp.json()
    assert user["name"] == payload["name"]
    assert user["email"] == payload["email"]

    fetch_resp = client.get(f"/users/{user['id']}")
    assert fetch_resp.status_code == 200
    assert fetch_resp.json()["email"] == payload["email"]


def test_video_upload_and_task_status_flow(client, monkeypatch):
    task_id = f"task-{uuid.uuid4().hex}"

    class DummyAsyncResult:
        def __init__(self, identifier):
            self.id = identifier

    monkeypatch.setattr(
        videos_router.process_video_task,
        "delay",
        lambda *args, **kwargs: DummyAsyncResult(task_id),
    )

    fake_content = io.BytesIO(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
    resp = client.post(
        "/videos/upload",
        files={"file": ("sample.mp4", fake_content, "video/mp4")},
        data={"title": f"Integration Upload {uuid.uuid4().hex}"},
    )
    assert resp.status_code == 200, resp.text
    upload_payload = resp.json()
    assert upload_payload["task_id"] == task_id
    assert upload_payload["status"] == "processing"
    video_id = upload_payload["video_id"]

    class CompletedAsyncResult:
        def __init__(self, result):
            self.state = "SUCCESS"
            self.result = result

    monkeypatch.setattr(
        videos_router.process_video_task,
        "AsyncResult",
        lambda *_: CompletedAsyncResult({"video_id": video_id, "status": "completed"}),
    )

    status_resp = client.get(f"/videos/tasks/{task_id}")
    assert status_resp.status_code == 200
    task_payload = status_resp.json()
    assert task_payload["status"] == "SUCCESS"
    assert task_payload["result"]["video_id"] == video_id


def test_video_search_returns_results(client, monkeypatch, tmp_path):
    video = _insert_video(title="Searchable Integration Video")

    mapping_path = tmp_path / "faiss_mapping.pkl"
    with mapping_path.open("wb") as handle:
        pickle.dump({0: video.id}, handle)

    monkeypatch.setattr(videos_router.settings, "FAISS_MAPPING_PATH", str(mapping_path))

    class FakeIndex:
        def __init__(self):
            self.ntotal = 1

    monkeypatch.setattr(videos_router, "load_index_if_exists", lambda dim: FakeIndex())
    monkeypatch.setattr(
        videos_router,
        "search_vector",
        lambda vec, k: (np.array([0.05], dtype="float32"), np.array([0])),
    )
    monkeypatch.setattr(
        videos_router,
        "generate_embedding",
        lambda q: np.array([0.0, 0.0, 0.0], dtype="float32"),
    )

    resp = client.get("/videos/search", params={"q": "integration", "k": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["video_id"] == video.id
    assert data[0]["title"] == video.title
    assert data[0]["path"] is not None
    assert data[0]["similarity_score"] > 0


def test_delete_video_flow(client):
    video = _insert_video(status="ready-for-delete", path=None)

    resp = client.delete(f"/videos/{video.id}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["id"] == video.id
    assert payload["message"] == "Video deleted successfully"
