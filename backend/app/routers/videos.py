from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.concurrency import run_in_threadpool
import logging

from sqlalchemy.orm import Session
from typing import List

from app.core.database import get_db
from app import models
from app.schemas import VideoRead
from app.schemas.transcripts import TranscriptRead
from app.tasks.video_tasks import process_video_task
from app.processing.adapters.direct_upload_compatibility import (
    DIRECT_PROCESSING_DEPRECATION_WARNING,
    upload_video_compatibility,
)

router = APIRouter()
logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("uvicorn.error")


@router.post("/upload", deprecated=True)
async def upload_video(
    file: UploadFile = File(...),
    title: str = Form(...),
    owner_id: int | None = Form(None),
    db: Session = Depends(get_db)
):
    return await upload_video_compatibility(file=file, title=title, owner_id=owner_id, db=db)


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
