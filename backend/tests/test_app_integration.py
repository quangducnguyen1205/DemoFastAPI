import io
import pickle
import sys
import uuid

import numpy as np

from app import models
from app.core.database import SessionLocal
import app.routers.videos as videos_router
import app.services.semantic_index as semantic_index
import app.services.semantic_index.reader as semantic_reader
import app.services.semantic_index.writer as semantic_writer
import app.services.video_processing as video_processing


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


def test_generate_embeddings_batches_single_encode_call(monkeypatch):
    class FakeModel:
        def __init__(self):
            self.calls = []

        def encode(self, texts):
            self.calls.append(list(texts))
            return np.array([[1.0, 2.0], [3.0, 4.0]], dtype="float32")

    fake_model = FakeModel()
    monkeypatch.setattr(semantic_index, "_embedding_model", fake_model)

    result = semantic_index.generate_embeddings(["first", "second"])

    assert fake_model.calls == [["first", "second"]]
    assert result.shape == (2, 2)


def test_get_whisper_model_caches_per_process(monkeypatch):
    class FakeWhisperModule:
        def __init__(self):
            self.calls = 0

        def load_model(self, model_name):
            self.calls += 1
            return {"model_name": model_name}

    fake_whisper = FakeWhisperModule()
    monkeypatch.setattr(video_processing, "_whisper_model", None)
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

    first = video_processing.get_whisper_model()
    second = video_processing.get_whisper_model()

    assert first == {"model_name": "base"}
    assert second == first
    assert fake_whisper.calls == 1


def test_writer_recreates_index_when_existing_file_is_unreadable(monkeypatch, tmp_path):
    index_path = tmp_path / "faiss_index.faiss"
    index_path.write_bytes(b"placeholder")

    class FakeIndex:
        def __init__(self, dim):
            self.d = dim

    class FakeFaiss:
        def read_index(self, path):
            raise RuntimeError("Resource deadlock avoided")

        def IndexFlatL2(self, dim):
            return FakeIndex(dim)

    monkeypatch.setattr(semantic_writer, "FAISS_INDEX_PATH", str(index_path))
    monkeypatch.setattr(semantic_writer, "_faiss", FakeFaiss())
    monkeypatch.setattr(semantic_writer, "_index", None)

    index = semantic_writer.load_or_create_index(384)

    assert index.d == 384
    unreadable_backups = list(tmp_path.glob("faiss_index.faiss.unreadable.*"))
    assert unreadable_backups


def test_writer_recreates_mapping_when_existing_file_is_unreadable(monkeypatch, tmp_path):
    mapping_path = tmp_path / "faiss_mapping.pkl"
    mapping_path.write_bytes(b"placeholder")

    monkeypatch.setattr(semantic_writer, "FAISS_MAPPING_PATH", str(mapping_path))
    monkeypatch.setattr(semantic_writer, "_mapping", None)
    monkeypatch.setattr(semantic_writer.pickle, "load", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(35, "Resource deadlock avoided")))

    mapping = semantic_writer._load_mapping()

    assert mapping == {}
    unreadable_backups = list(tmp_path.glob("faiss_mapping.pkl.unreadable.*"))
    assert unreadable_backups


def test_reader_returns_empty_index_when_existing_file_is_unreadable(monkeypatch, tmp_path):
    index_path = tmp_path / "faiss_index.faiss"
    index_path.write_bytes(b"placeholder")

    class FakeIndex:
        def __init__(self, dim):
            self.d = dim
            self.ntotal = 0

    class FakeFaiss:
        def read_index(self, path):
            raise RuntimeError("Resource deadlock avoided")

        def IndexFlatL2(self, dim):
            return FakeIndex(dim)

    monkeypatch.setattr(semantic_reader, "FAISS_INDEX_PATH", str(index_path))
    monkeypatch.setattr(semantic_reader, "_faiss", FakeFaiss())
    monkeypatch.setattr(semantic_reader, "_index", None)

    index = semantic_reader.load_index_if_exists(384)

    assert index.d == 384
    unreadable_backups = list(tmp_path.glob("faiss_index.faiss.unreadable.*"))
    assert unreadable_backups
