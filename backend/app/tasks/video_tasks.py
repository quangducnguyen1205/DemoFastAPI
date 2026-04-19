import logging
import tempfile
import time
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.celery_app import celery_app
from app import models
from app.services.video_processing import (
    extract_audio_to_wav,
    transcribe_audio_with_whisper,
    segment_text,
    persist_transcript_segments,
)

logger = logging.getLogger(__name__)


def _get_db_session() -> Session:
    return SessionLocal()


def _log_timing(metric: str, value_ms: float, *, task_id: str | None = None, video_id: int | None = None, **extra) -> None:
    parts = [f"{metric}={value_ms:.2f}", f"task_id={task_id}", f"video_id={video_id}"]
    parts.extend(f"{key}={value}" for key, value in extra.items() if value is not None)
    logger.info(" ".join(parts))


@celery_app.task(name="process_video", bind=True)
def process_video_task(self, video_id: int, abs_video_path: str) -> dict:
    task_started_at = time.perf_counter()
    task_id = getattr(self.request, "id", None)
    db = _get_db_session()
    try:
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        if not video:
            result = {"status": "failed", "error": f"Video {video_id} not found"}
            _log_timing("total_task_ms", (time.perf_counter() - task_started_at) * 1000, task_id=task_id, video_id=video_id, status=result["status"])
            return result

        try:
            with tempfile.TemporaryDirectory(prefix="vp_") as temp_dir:
                ffmpeg_started_at = time.perf_counter()
                audio_path = extract_audio_to_wav(abs_video_path, temp_dir=temp_dir)
                _log_timing("ffmpeg_ms", (time.perf_counter() - ffmpeg_started_at) * 1000, task_id=task_id, video_id=video_id)

                whisper_started_at = time.perf_counter()
                full_text = transcribe_audio_with_whisper(audio_path)
                _log_timing("whisper_ms", (time.perf_counter() - whisper_started_at) * 1000, task_id=task_id, video_id=video_id)

            chunking_started_at = time.perf_counter()
            segments = segment_text(full_text) if full_text else []
            _log_timing(
                "chunking_ms",
                (time.perf_counter() - chunking_started_at) * 1000,
                task_id=task_id,
                video_id=video_id,
                segment_count=len(segments),
            )

            if segments:
                transcript_started_at = time.perf_counter()
                persist_transcript_segments(db, video.id, segments)
                _log_timing(
                    "transcript_persist_ms",
                    (time.perf_counter() - transcript_started_at) * 1000,
                    task_id=task_id,
                    video_id=video_id,
                    segment_count=len(segments),
                )
            else:
                _log_timing("transcript_persist_ms", 0.0, task_id=task_id, video_id=video_id, segment_count=0)

            video.status = "ready"
            db.commit()
            result = {"status": "ready", "segments": segments}
            _log_timing("total_task_ms", (time.perf_counter() - task_started_at) * 1000, task_id=task_id, video_id=video_id, status=result["status"], segment_count=len(segments))
            return result
        except Exception as e:
            logger.exception("Processing failed")
            video.status = "failed"
            db.commit()
            result = {"status": "failed", "error": str(e)}
            _log_timing("total_task_ms", (time.perf_counter() - task_started_at) * 1000, task_id=task_id, video_id=video_id, status=result["status"])
            return result
    finally:
        db.close()
