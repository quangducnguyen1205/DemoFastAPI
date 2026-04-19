import app.tasks.video_tasks as video_tasks
from app import models
from app.core.database import SessionLocal


def _insert_video(**overrides):
    defaults = {
        "title": "Worker Fixture",
        "description": "worker test fixture",
        "url": "videos/worker-fixture.mp4",
        "path": "videos/worker-fixture.mp4",
        "owner_id": None,
        "status": "processing",
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


def test_process_video_task_persists_transcript_and_marks_video_ready(monkeypatch):
    video = _insert_video()

    monkeypatch.setattr(video_tasks, "extract_audio_to_wav", lambda abs_video_path, temp_dir: f"{temp_dir}/audio.wav")
    monkeypatch.setattr(
        video_tasks,
        "transcribe_audio_with_whisper",
        lambda _audio_path: "First sentence for processing. Second sentence stays with the transcript.",
    )

    result = video_tasks.process_video_task.run(video.id, "/tmp/video.mp4")

    db = SessionLocal()
    try:
        refreshed = db.query(models.Video).filter(models.Video.id == video.id).first()
        transcripts = (
            db.query(models.Transcript)
            .filter(models.Transcript.video_id == video.id)
            .order_by(models.Transcript.segment_index)
            .all()
        )
        assert refreshed is not None
        assert refreshed.status == "ready"
        assert [item.text for item in transcripts] == result["segments"]
        assert transcripts
        assert result["status"] == "ready"
    finally:
        db.close()


def test_process_video_task_missing_video_returns_failed():
    result = video_tasks.process_video_task.run(999999, "/tmp/missing.mp4")

    assert result["status"] == "failed"
    assert "not found" in result["error"]


def test_process_video_task_marks_video_failed_when_processing_raises(monkeypatch):
    video = _insert_video()

    monkeypatch.setattr(
        video_tasks,
        "extract_audio_to_wav",
        lambda abs_video_path, temp_dir: (_ for _ in ()).throw(RuntimeError("ffmpeg failed")),
    )

    result = video_tasks.process_video_task.run(video.id, "/tmp/video.mp4")

    db = SessionLocal()
    try:
        refreshed = db.query(models.Video).filter(models.Video.id == video.id).first()
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert result["status"] == "failed"
        assert "ffmpeg failed" in result["error"]
    finally:
        db.close()


def test_process_video_task_ready_with_empty_transcript_preserves_contract(monkeypatch):
    video = _insert_video()

    monkeypatch.setattr(video_tasks, "extract_audio_to_wav", lambda abs_video_path, temp_dir: f"{temp_dir}/audio.wav")
    monkeypatch.setattr(video_tasks, "transcribe_audio_with_whisper", lambda _audio_path: None)

    result = video_tasks.process_video_task.run(video.id, "/tmp/video.mp4")

    db = SessionLocal()
    try:
        refreshed = db.query(models.Video).filter(models.Video.id == video.id).first()
        transcript_count = db.query(models.Transcript).filter(models.Transcript.video_id == video.id).count()
        assert refreshed is not None
        assert refreshed.status == "ready"
        assert transcript_count == 0
        assert result == {"status": "ready", "segments": []}
    finally:
        db.close()
