import logging
import os
import shutil
import time
import uuid

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app import models
from app.config.settings import settings

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("uvicorn.error")
DIRECT_PROCESSING_DEPRECATION_WARNING = (
    "event=direct_processing_endpoint_deprecated "
    "retained_for=rollback_compatibility replacement=project3_kafka_consumer"
)


async def upload_video_compatibility(
    *,
    file: UploadFile,
    title: str,
    owner_id: int | None,
    db: Session,
) -> dict:
    logger.warning(DIRECT_PROCESSING_DEPRECATION_WARNING)
    upload_started_at = time.perf_counter()
    content_type = file.content_type or ""
    if not content_type.startswith("video/"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file type '{content_type}'. Only video files are allowed.",
        )

    def _sync_upload_video() -> dict:
        from app.tasks.video_tasks import process_video_task

        video_dir = settings.VIDEO_DIR
        os.makedirs(video_dir, exist_ok=True)
        original_ext = os.path.splitext(file.filename)[1]
        unique_name = f"{uuid.uuid4().hex}{original_ext}"
        save_path = os.path.join(video_dir, unique_name)
        file_save_started_at = time.perf_counter()
        file.file.seek(0)
        with open(save_path, "wb") as out_file:
            shutil.copyfileobj(file.file, out_file, length=1024 * 1024)
            bytes_written = out_file.tell()
        timing_logger.info(
            "file_save_ms=%.2f path=%s bytes=%s",
            (time.perf_counter() - file_save_started_at) * 1000,
            save_path,
            bytes_written,
        )

        rel_path = os.path.join(os.path.basename(settings.VIDEO_SUBDIR), unique_name)
        video = models.Video(
            title=title,
            url=rel_path,
            path=rel_path,
            owner_id=owner_id,
            status="processing",
        )
        db.add(video)
        db.commit()
        db.refresh(video)
        abs_video_path = os.path.abspath(save_path)
        async_result = process_video_task.delay(video.id, abs_video_path)
        timing_logger.info(
            "upload_request_ms=%.2f video_id=%s task_id=%s",
            (time.perf_counter() - upload_started_at) * 1000,
            video.id,
            async_result.id,
        )
        return {"task_id": async_result.id, "status": "processing", "video_id": video.id}

    return await run_in_threadpool(_sync_upload_video)
