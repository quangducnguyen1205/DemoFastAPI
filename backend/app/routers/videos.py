from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.concurrency import run_in_threadpool
import os
import uuid
import logging
import shutil
import time

from sqlalchemy.orm import Session
from typing import List

from app.core.database import get_db
from app import models
from app.schemas import VideoRead
from app.schemas.transcripts import TranscriptRead
from app.tasks.video_tasks import process_video_task
from app.config.settings import settings

router = APIRouter()
timing_logger = logging.getLogger("uvicorn.error")


@router.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    title: str = Form(...),
    owner_id: int | None = Form(None),
    db: Session = Depends(get_db)
):
    upload_started_at = time.perf_counter()

    # Validate MIME type
    content_type = file.content_type or ""
    if not content_type.startswith("video/"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file type '{content_type}'. Only video files are allowed.",
        )

    def _sync_upload_video():
        video_dir = settings.VIDEO_DIR
        os.makedirs(video_dir, exist_ok=True)

        # Generate unique filename preserving extension
        original_ext = os.path.splitext(file.filename)[1]
        unique_name = f"{uuid.uuid4().hex}{original_ext}"
        save_path = os.path.join(video_dir, unique_name)

        # Persist a file to disk
        file_save_started_at = time.perf_counter()
        file.file.seek(0)
        with open(save_path, "wb") as out_file:
            shutil.copyfileobj(file.file, out_file, length=1024 * 1024)
            bytes_written = out_file.tell()
        file_save_ms = (time.perf_counter() - file_save_started_at) * 1000
        timing_logger.info("file_save_ms=%.2f path=%s bytes=%s", file_save_ms, save_path, bytes_written)

        # Store relative path under the media root (e.g., "videos/<file>")
        rel_path = os.path.join(os.path.basename(settings.VIDEO_SUBDIR), unique_name) if hasattr(settings, 'VIDEO_SUBDIR') else os.path.join("videos", unique_name)

        db_video = models.Video(
            title=title,
            url=rel_path,   # keeping url for backward compatibility
            path=rel_path,
            owner_id=owner_id,
            status="processing",
        )
        db.add(db_video)
        db.commit()
        db.refresh(db_video)

        # Enqueue background processing task
        abs_video_path = os.path.abspath(save_path)
        async_result = process_video_task.delay(db_video.id, abs_video_path)

        response = {"task_id": async_result.id, "status": "processing", "video_id": db_video.id}
        upload_request_ms = (time.perf_counter() - upload_started_at) * 1000
        timing_logger.info("upload_request_ms=%.2f video_id=%s task_id=%s", upload_request_ms, db_video.id, async_result.id)
        return response

    return await run_in_threadpool(_sync_upload_video)


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    def _sync_get_task_status():
        res = process_video_task.AsyncResult(task_id)
        state = res.state
        payload = {"status": state}
        if state == "SUCCESS":
            payload["result"] = res.result
        elif state == "FAILURE":
            payload["error"] = str(res.result)
        return payload

    return await run_in_threadpool(_sync_get_task_status)

@router.get("/{video_id}", response_model=VideoRead)
async def get_video(video_id: int, db: Session = Depends(get_db)):
    def _sync_get_video():
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found")
        return video

    return await run_in_threadpool(_sync_get_video)


# Get video transcript
@router.get("/{video_id}/transcript", response_model=List[TranscriptRead])
async def get_video_transcript(video_id: int, db: Session = Depends(get_db)):
    """Get transcript segments for a specific video, ordered by segment index."""
    def _sync_get_transcript():
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        query = db.query(models.Transcript).filter(models.Transcript.video_id == video_id)

        if hasattr(models.Transcript, "segment_index"):
            query = query.order_by(models.Transcript.segment_index)

        return query.all()

    return await run_in_threadpool(_sync_get_transcript)
