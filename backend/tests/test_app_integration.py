import io
import uuid

from app import models
from app.core.database import SessionLocal
import app.routers.videos as videos_router


def _insert_video(**overrides):
    defaults = {
        "title": f"Integration Video {uuid.uuid4().hex}",
        "description": "integration fixture",
        "url": f"videos/{uuid.uuid4().hex}.mp4",
        "path": f"videos/{uuid.uuid4().hex}.mp4",
        "owner_id": None,
        "status": "ready",
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


def _patch_celery(monkeypatch, task_id: str = "task-id"):
    class DummyAsyncResult:
        def __init__(self, identifier):
            self.id = identifier

    monkeypatch.setattr(
        videos_router.process_video_task,
        "delay",
        lambda *args, **kwargs: DummyAsyncResult(task_id),
    )


def test_root_endpoint_describes_processing_service(client):
    resp = client.get("/")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["message"] == "AI Knowledge Workspace Processing Service"
    assert payload["docs"] == "/docs"


def test_health_endpoint(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_openapi_excludes_removed_product_routes(client):
    resp = client.get("/openapi.json")

    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/videos/upload" in paths
    assert "/videos/tasks/{task_id}" in paths
    assert "/videos/{video_id}" in paths
    assert "/videos/{video_id}/transcript" in paths
    assert "/videos/search" not in paths
    assert "/users/" not in paths
    assert "/auth/login" not in paths


def test_upload_video_returns_processing_contract(client, monkeypatch):
    _patch_celery(monkeypatch, task_id="processing-task")
    file_bytes = io.BytesIO(b"fake-video")

    resp = client.post(
        "/videos/upload",
        files={"file": ("video.mp4", file_bytes, "video/mp4")},
        data={"title": "Processing Upload"},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["task_id"] == "processing-task"
    assert payload["status"] == "processing"
    assert isinstance(payload["video_id"], int)


def test_upload_video_accepts_legacy_owner_id_for_compatibility(client, monkeypatch):
    _patch_celery(monkeypatch, task_id="legacy-owner")
    file_bytes = io.BytesIO(b"owner video")

    resp = client.post(
        "/videos/upload",
        files={"file": ("video.mp4", file_bytes, "video/mp4")},
        data={"title": "Owned Upload", "owner_id": "123"},
    )

    assert resp.status_code == 200
    video_id = resp.json()["video_id"]

    db = SessionLocal()
    try:
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        assert video is not None
        assert video.owner_id == 123
    finally:
        db.close()


def test_upload_invalid_format_returns_422(client, monkeypatch):
    _patch_celery(monkeypatch)
    file_bytes = io.BytesIO(b"not-a-video")

    resp = client.post(
        "/videos/upload",
        files={"file": ("file.txt", file_bytes, "text/plain")},
        data={"title": "Text"},
    )

    assert resp.status_code == 422


def test_task_status_pending(client, monkeypatch):
    class PendingResult:
        state = "PENDING"

    monkeypatch.setattr(videos_router.process_video_task, "AsyncResult", lambda *_: PendingResult())

    resp = client.get("/videos/tasks/pending-id")

    assert resp.status_code == 200
    assert resp.json() == {"status": "PENDING"}


def test_task_status_success(client, monkeypatch):
    class SuccessResult:
        state = "SUCCESS"
        result = {"status": "ready", "segments": ["Hello", "World"]}

    monkeypatch.setattr(videos_router.process_video_task, "AsyncResult", lambda *_: SuccessResult())

    resp = client.get("/videos/tasks/success-id")

    assert resp.status_code == 200
    assert resp.json() == {"status": "SUCCESS", "result": {"status": "ready", "segments": ["Hello", "World"]}}


def test_task_status_failure(client, monkeypatch):
    class FailureResult:
        state = "FAILURE"
        result = RuntimeError("boom")

    monkeypatch.setattr(videos_router.process_video_task, "AsyncResult", lambda *_: FailureResult())

    resp = client.get("/videos/tasks/failure-id")

    assert resp.status_code == 200
    assert resp.json() == {"status": "FAILURE", "error": "boom"}


def test_get_video_returns_processing_state(client):
    video = _insert_video(status="processing")

    resp = client.get(f"/videos/{video.id}")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["id"] == video.id
    assert payload["status"] == "processing"


def test_get_video_transcript_returns_segments_ordered(client):
    video = _insert_video(title="Transcript Test")

    db = SessionLocal()
    try:
        db.add_all(
            [
                models.Transcript(video_id=video.id, text="Hello", segment_index=0),
                models.Transcript(video_id=video.id, text="World", segment_index=1),
            ]
        )
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/videos/{video.id}/transcript")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload[0]["text"] == "Hello"
    assert payload[1]["text"] == "World"


def test_get_video_transcript_unknown_video_returns_404(client):
    resp = client.get("/videos/999999/transcript")

    assert resp.status_code == 404
