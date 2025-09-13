import io
import uuid
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
import app.routers.videos as videos


def test_video_upload_creates_record_and_returns_task(client, monkeypatch):
    # Patch Celery task dispatch to avoid invoking real worker/heavy deps
    class DummyAsyncResult:
        def __init__(self):
            self.id = "dummy-task-id"
    monkeypatch.setattr(videos.process_video_task, 'delay', lambda *args, **kwargs: DummyAsyncResult())

    # Prepare a tiny fake MP4 file (not a valid video but sufficient for saving)
    fake_content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"  # minimal mp4 header-like bytes
    file_obj = io.BytesIO(fake_content)
    filename = "sample.mp4"

    unique_title = f"Test Upload {uuid.uuid4()}"

    # Count videos before -> to compare after upload
    db: Session = SessionLocal()
    try:
        before_count = db.query(videos.models.Video).count()
    finally:
        db.close()

    # Perform upload
    resp = client.post(
        "/videos/upload",
        files={"file": (filename, file_obj, "video/mp4")},
        data={"title": unique_title}
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "processing"
    assert payload["task_id"] == "dummy-task-id"
    assert isinstance(payload.get("video_id"), int) and payload["video_id"] > 0

    # Verify DB record created with correct title
    db2: Session = SessionLocal()
    try:
        after_count = db2.query(videos.models.Video).count()
        created = db2.query(videos.models.Video).filter_by(id=payload["video_id"]).first()
    finally:
        db2.close()

    assert after_count == before_count + 1
    assert created is not None
    assert created.title == unique_title
    assert created.status == "processing"
