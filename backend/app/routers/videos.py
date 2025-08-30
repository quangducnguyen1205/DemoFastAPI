from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
import os
import uuid
import logging

from pathlib import Path
from sqlalchemy.orm import Session
from typing import List

from ..core.database import get_db
from .. import models
from ..services import semantic_index
from ..schemas import VideoSearchResult, VideoCreate, VideoRead
from ..tasks.video_tasks import process_video_task
from ..config.settings import settings

router = APIRouter()

MEDIA_ROOT = settings.MEDIA_ROOT
VIDEO_DIR = settings.VIDEO_DIR
os.makedirs(VIDEO_DIR, exist_ok=True)


## split_transcript_text moved to app/utils.py and imported above


@router.get("/search", response_model=List[VideoSearchResult])
def search_videos(q: str, k: int = 5, db: Session = Depends(get_db)):
    """Semantic search over video transcripts using FAISS embeddings.

    Args:
        q: Query string to search for semantically.
        k: Number of results to return (default 5).
    Returns: List of VideoSearchResult with similarity scores (higher = closer).
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty")
    try:
        # Generate query embedding
        query_vec = semantic_index.generate_embedding(q)
        import numpy as np, faiss  # type: ignore
        vec = query_vec.astype('float32') if hasattr(query_vec, 'astype') else np.array(query_vec, dtype='float32')
        dim = vec.shape[0]
        # Load index & mapping
        index = semantic_index.load_faiss_index(dim)
        mapping = semantic_index.load_faiss_mapping()
        if index.ntotal == 0 or not mapping:
            return []
        # Perform search across entire index, then group by video id
        nprobe = min(max(50, k * 10), index.ntotal)  # heuristic
        D, I = index.search(vec.reshape(1, -1), nprobe)
        distances = D[0]
        indices = I[0]

        best_by_video: dict[int, float] = {}
        for faiss_id, dist in zip(indices, distances):
            if faiss_id == -1:
                continue
            vid = mapping.get(faiss_id)
            if not vid:
                continue
            # Convert L2 distance to similarity
            similarity = 1.0 / (1.0 + float(dist)) if dist >= 0 else 0.0
            if vid not in best_by_video or similarity > best_by_video[vid]:
                best_by_video[vid] = similarity

        # Build response objects (one per video) and sort by similarity desc
        results: List[VideoSearchResult] = []
        for vid, sim in best_by_video.items():
            video = db.query(models.Video).filter(models.Video.id == vid).first()
            if not video:
                continue
            results.append(
                VideoSearchResult(
                    video_id=video.id,
                    title=video.title,
                    path=video.path or video.url,
                    similarity_score=sim,
                )
            )
        results.sort(key=lambda r: r.similarity_score, reverse=True)
        return results[:k]
    except HTTPException:
        raise
    except Exception as e:
        logging.warning(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail="Search failed")

# Upload video file
@router.post("/upload")
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

    # Store relative path under media root (e.g., "videos/<file>")
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

    return {"task_id": async_result.id, "status": "processing", "video_id": db_video.id}

@router.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    res = process_video_task.AsyncResult(task_id)
    state = res.state
    payload = {"status": state}
    if state == "SUCCESS":
        payload["result"] = res.result
    elif state == "FAILURE":
        payload["error"] = str(res.result)
    return payload

# Create video
@router.post("/", response_model=VideoRead)
def create_video(video: VideoCreate, db: Session = Depends(get_db)):
    db_video = models.Video(title=video.title, description=video.description, url=video.url)
    db.add(db_video)
    db.commit()
    db.refresh(db_video)
    return db_video

# List videos
@router.get("/", response_model=List[VideoRead])
def list_videos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    videos = db.query(models.Video).offset(skip).limit(limit).all()
    return videos

# Get single video
@router.get("/{video_id}", response_model=VideoRead)
def get_video(video_id: int, db: Session = Depends(get_db)):
    video = db.query(models.Video).filter(models.Video.id == video_id).first()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video

# Update video (full update)
@router.put("/{video_id}", response_model=VideoRead)
def update_video(video_id: int, video_in: VideoCreate, db: Session = Depends(get_db)):
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
