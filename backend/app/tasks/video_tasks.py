import logging
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.celery_app import celery_app
from .. import models
from ..services.video_processing import (
    extract_audio_to_wav,
    transcribe_audio_with_whisper,
    segment_text,
    persist_transcript_segments,
    embed_and_update_faiss,
)


def _get_db_session() -> Session:
    return SessionLocal()


@celery_app.task(name="process_video")
def process_video_task(video_id: int, abs_video_path: str) -> dict:
    db = _get_db_session()
    try:
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        if not video:
            return {"status": "failed", "error": f"Video {video_id} not found"}

        try:
            audio_path = extract_audio_to_wav(abs_video_path)
            full_text = transcribe_audio_with_whisper(audio_path)
            segments = segment_text(full_text, max_len=200) if full_text else []

            if segments:
                persist_transcript_segments(db, video.id, segments)
                embed_and_update_faiss(segments, video.id)

            video.status = "ready"
            db.commit()
            return {"status": "ready", "segments": segments}
        except Exception as e:
            logging.exception("Processing failed")
            video.status = "failed"
            db.commit()
            return {"status": "failed", "error": str(e)}
    finally:
        db.close()
