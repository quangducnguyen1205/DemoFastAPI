import logging
import tempfile
import time
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.celery_app import celery_app
from app import models
from app.services.object_storage import get_object_storage_client
from app.services.processing_outbox import (
    add_processing_failed_event,
    add_transcript_ready_event,
)
from app.services.video_processing import (
    extract_audio_to_wav,
    transcribe_audio_with_whisper,
    segment_text,
    persist_transcript_segments,
)

logger = logging.getLogger(__name__)


def _get_db_session() -> Session:
    return SessionLocal()


def _log_timing(
    metric: str,
    value_ms: float,
    *,
    task_id: str | None = None,
    video_id: int | None = None,
    asset_id: str | None = None,
    **extra,
) -> None:
    parts = [
        f"{metric}={value_ms:.2f}",
        f"task_id={task_id}",
        f"video_id={video_id}",
        f"asset_id={asset_id}",
    ]
    parts.extend(f"{key}={value}" for key, value in extra.items() if value is not None)
    logger.info(" ".join(parts))


def _transcribe_video_file(
    abs_video_path: str,
    *,
    task_id: str | None = None,
    video_id: int | None = None,
    asset_id: str | None = None,
) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="vp_") as temp_dir:
        ffmpeg_started_at = time.perf_counter()
        audio_path = extract_audio_to_wav(abs_video_path, temp_dir=temp_dir)
        _log_timing(
            "ffmpeg_ms",
            (time.perf_counter() - ffmpeg_started_at) * 1000,
            task_id=task_id,
            video_id=video_id,
            asset_id=asset_id,
        )

        whisper_started_at = time.perf_counter()
        full_text = transcribe_audio_with_whisper(audio_path)
        _log_timing(
            "whisper_ms",
            (time.perf_counter() - whisper_started_at) * 1000,
            task_id=task_id,
            video_id=video_id,
            asset_id=asset_id,
        )

    chunking_started_at = time.perf_counter()
    segments = segment_text(full_text) if full_text else []
    _log_timing(
        "chunking_ms",
        (time.perf_counter() - chunking_started_at) * 1000,
        task_id=task_id,
        video_id=video_id,
        asset_id=asset_id,
        segment_count=len(segments),
    )
    return segments


def _persist_processing_request_transcript_segments(
    db: Session,
    *,
    event_id: str,
    segments: list[str],
) -> None:
    db.query(models.ProcessingRequestTranscript).filter(
        models.ProcessingRequestTranscript.processing_request_event_id == event_id,
    ).delete(synchronize_session=False)
    for idx, segment in enumerate(segments):
        db.add(
            models.ProcessingRequestTranscript(
                processing_request_event_id=event_id,
                segment_index=idx,
                text=segment,
                start_seconds=None,
                end_seconds=None,
            )
        )


@celery_app.task(name="process_video", bind=True)
def process_video_task(self, video_id: int, abs_video_path: str) -> dict:
    task_started_at = time.perf_counter()
    task_id = getattr(self.request, "id", None)
    db = _get_db_session()
    try:
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        if not video:
            result = {"status": "failed", "error": f"Video {video_id} not found"}
            _log_timing(
                "total_task_ms",
                (time.perf_counter() - task_started_at) * 1000,
                task_id=task_id,
                video_id=video_id,
                status=result["status"],
            )
            return result

        try:
            segments = _transcribe_video_file(abs_video_path, task_id=task_id, video_id=video_id)

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
            _log_timing(
                "total_task_ms",
                (time.perf_counter() - task_started_at) * 1000,
                task_id=task_id,
                video_id=video_id,
                status=result["status"],
                segment_count=len(segments),
            )
            return result
        except Exception as e:
            logger.exception("Processing failed")
            video.status = "failed"
            db.commit()
            result = {"status": "failed", "error": str(e)}
            _log_timing(
                "total_task_ms",
                (time.perf_counter() - task_started_at) * 1000,
                task_id=task_id,
                video_id=video_id,
                status=result["status"],
            )
            return result
    finally:
        db.close()


@celery_app.task(name="process_asset_object", bind=True)
def process_asset_object_task(self, request: dict) -> dict:
    task_started_at = time.perf_counter()
    task_id = getattr(self.request, "id", None)
    event_id = request["eventId"]
    asset_id = request["assetId"]
    bucket = request["bucket"]
    object_key = request["objectKey"]
    original_filename = request.get("originalFilename")
    content_type = request["contentType"]

    logger.info(
        "starting asset object processing event_id=%s asset_id=%s bucket=%s object_key=%s content_type=%s task_id=%s",
        event_id,
        asset_id,
        bucket,
        object_key,
        content_type,
        task_id,
    )

    db = _get_db_session()
    try:
        updated = (
            db.query(models.ProcessingRequest)
            .filter(
                models.ProcessingRequest.event_id == event_id,
                models.ProcessingRequest.status.in_(["accepted", "enqueued"]),
            )
            .update({"status": "processing", "error": None}, synchronize_session=False)
        )
        db.commit()

        if updated == 0:
            existing = db.query(models.ProcessingRequest).filter(
                models.ProcessingRequest.event_id == event_id,
            ).first()
            status = existing.status if existing else "missing"
            logger.info(
                "skipping duplicate asset object task event_id=%s asset_id=%s status=%s",
                event_id,
                asset_id,
                status,
            )
            return {"status": status, "asset_id": asset_id, "duplicate": True}

        try:
            with tempfile.TemporaryDirectory(prefix="asset_") as temp_dir:
                download_started_at = time.perf_counter()
                abs_video_path = get_object_storage_client().download_to_file(
                    bucket=bucket,
                    object_key=object_key,
                    destination_dir=temp_dir,
                    filename=original_filename,
                )
                _log_timing(
                    "object_download_ms",
                    (time.perf_counter() - download_started_at) * 1000,
                    task_id=task_id,
                    asset_id=asset_id,
                    bucket=bucket,
                    object_key=object_key,
                )
                segments = _transcribe_video_file(abs_video_path, task_id=task_id, asset_id=asset_id)

            processing_request = db.query(models.ProcessingRequest).filter(
                models.ProcessingRequest.event_id == event_id,
            ).one()
            _persist_processing_request_transcript_segments(db, event_id=event_id, segments=segments)
            processing_request.status = "ready"
            processing_request.segment_count = len(segments)
            processing_request.error = None
            add_transcript_ready_event(
                db,
                processing_request=processing_request,
                segment_count=len(segments),
            )
            db.commit()
            result = {"status": "ready", "asset_id": asset_id, "segments": segments}
            _log_timing(
                "total_task_ms",
                (time.perf_counter() - task_started_at) * 1000,
                task_id=task_id,
                asset_id=asset_id,
                status=result["status"],
                segment_count=len(segments),
            )
            return result
        except Exception as e:
            logger.exception("Asset object processing failed event_id=%s asset_id=%s", event_id, asset_id)
            db.rollback()
            processing_request = db.query(models.ProcessingRequest).filter(
                models.ProcessingRequest.event_id == event_id,
            ).first()
            if processing_request:
                processing_request.status = "failed"
                processing_request.error = str(e)
                add_processing_failed_event(
                    db,
                    processing_request=processing_request,
                    exc=e,
                )
                db.commit()
            result = {"status": "failed", "asset_id": asset_id, "error": str(e)}
            _log_timing(
                "total_task_ms",
                (time.perf_counter() - task_started_at) * 1000,
                task_id=task_id,
                asset_id=asset_id,
                status=result["status"],
            )
            return result
    finally:
        db.close()
