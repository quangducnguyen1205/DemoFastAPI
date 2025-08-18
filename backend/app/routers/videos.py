from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
import os
import uuid
import logging
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from .. import models, schemas

router = APIRouter()

MEDIA_ROOT = os.getenv("MEDIA_ROOT", "media")
VIDEO_SUBDIR = "videos"
VIDEO_DIR = os.path.join(MEDIA_ROOT, VIDEO_SUBDIR)
os.makedirs(VIDEO_DIR, exist_ok=True)

# Upload video file
@router.post("/upload", response_model=schemas.VideoUploadResponse)
def upload_video(
    file: UploadFile = File(...),
    title: str = Form(...),
    owner_id: int | None = Form(None),
    db: Session = Depends(get_db)
):
    # Generate unique filename preserving extension
    original_ext = os.path.splitext(file.filename)[1]
    unique_name = f"{uuid.uuid4().hex}{original_ext}"
    save_path = os.path.join(VIDEO_DIR, unique_name)

    # Persist file to disk
    with open(save_path, "wb") as out_file:
        out_file.write(file.file.read())

    rel_path = os.path.join(VIDEO_SUBDIR, unique_name)

    db_video = models.Video(
        title=title,
        url=rel_path,   # keeping url for backward compatibility
        path=rel_path,
        owner_id=owner_id,
    )
    db.add(db_video)
    db.commit()
    db.refresh(db_video)
    return db_video

# Create video
@router.post("/", response_model=schemas.VideoRead)
def create_video(video: schemas.VideoCreate, db: Session = Depends(get_db)):
    db_video = models.Video(title=video.title, description=video.description, url=video.url)
    db.add(db_video)
    db.commit()
    db.refresh(db_video)
    return db_video

# List videos
@router.get("/", response_model=List[schemas.VideoRead])
def list_videos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    videos = db.query(models.Video).offset(skip).limit(limit).all()
    return videos

# Get single video
@router.get("/{video_id}", response_model=schemas.VideoRead)
def get_video(video_id: int, db: Session = Depends(get_db)):
    video = db.query(models.Video).filter(models.Video.id == video_id).first()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video

# Update video (full update)
@router.put("/{video_id}", response_model=schemas.VideoRead)
def update_video(video_id: int, video_in: schemas.VideoCreate, db: Session = Depends(get_db)):
    video = db.query(models.Video).filter(models.Video.id == video_id).first()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    video.title = video_in.title
    video.description = video_in.description
    video.url = video_in.url
    db.commit()
    db.refresh(video)
    return video

# Delete video
@router.delete("/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    video = db.query(models.Video).filter(models.Video.id == video_id).first()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    # Attempt to remove associated file if path present
    if getattr(video, "path", None):
        # Build absolute path inside expected directory
        candidate_path = os.path.join(MEDIA_ROOT, video.path) if not os.path.isabs(video.path) else video.path
        try:
            abs_candidate = os.path.abspath(candidate_path)
            videos_root_abs = os.path.abspath(VIDEO_DIR)
            # Ensure the file resides within the videos directory to prevent path traversal
            if abs_candidate.startswith(videos_root_abs) and os.path.exists(abs_candidate):
                os.remove(abs_candidate)
            else:
                logging.warning(f"Skip deleting file (outside directory or missing): {candidate_path}")
        except Exception as e:
            logging.warning(f"Failed to delete video file '{candidate_path}': {e}")

    db.delete(video)
    db.commit()
    return {"message": "Video deleted successfully", "id": video_id}
