from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.concurrency import run_in_threadpool
import os
import uuid
import logging
import pickle

from sqlalchemy.orm import Session
from typing import List

from app.core.database import get_db
from app import models
from app.schemas import VideoSearchResult, VideoRead
from app.schemas.transcripts import TranscriptRead
from app.tasks.video_tasks import process_video_task
from app.config.settings import settings
from app.services.semantic_index import generate_embedding
from app.services.semantic_index.reader import load_index_if_exists, search_vector

router = APIRouter()

MEDIA_ROOT = settings.MEDIA_ROOT
VIDEO_DIR = settings.VIDEO_DIR
os.makedirs(VIDEO_DIR, exist_ok=True)


## split_transcript_text moved to app/utils.py and imported above


@router.get("/search", response_model=List[VideoSearchResult])
async def search_videos(q: str, k: int = 5, owner_id: int | None = None, db: Session = Depends(get_db)):
    """Semantic search over video transcripts using FAISS embeddings.

    Args:
        q: Query string to search for semantically.
        k: Number of results to return (default 5).
        owner_id: Optional owner filter for narrowing results.
    Returns: List of VideoSearchResult with similarity scores (higher = closer).
    """
    def _sync_search() -> List[VideoSearchResult]:
        if not q.strip():
            raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty")
        try:
            # Generate query embedding
            query_vec = generate_embedding(q)
            import numpy as np  # type: ignore
            vec = query_vec.astype('float32') if hasattr(query_vec, 'astype') else np.array(query_vec, dtype='float32')
            dim = int(vec.shape[0])

            # Load index & mapping (read-only)
            index = load_index_if_exists(dim)

            # Search more segments than requested videos to allow grouping
            topk_segments = min(max(k * 4, 50), getattr(index, "ntotal", 0) or k)
            if getattr(index, "ntotal", 0) == 0:
                return []
            distances, indices = search_vector(vec, topk_segments)

            # Load mapping without importing writer functions
            mapping_path = settings.FAISS_MAPPING_PATH
            if not os.path.exists(mapping_path):
                return []
            with open(mapping_path, "rb") as f:
                mapping: dict[int, int] = pickle.load(f)

            best_by_video: dict[int, float] = {}
            for faiss_id, dist in zip(indices, distances):
                if faiss_id == -1:
                    continue
                vid = mapping.get(int(faiss_id))
                if vid is None:
                    continue
                # Convert L2 distance to a bounded similarity score
                similarity = 1.0 / (1.0 + float(dist)) if dist >= 0 else 0.0
                if vid not in best_by_video or similarity > best_by_video[vid]:
                    best_by_video[vid] = similarity

            # Build response objects (one per video) and sort by similarity desc
            results: List[VideoSearchResult] = []
            if best_by_video:
                # Fetch videos individually; could be optimized to a single IN query if desired
                for vid, sim in best_by_video.items():
                    video = db.query(models.Video).filter(models.Video.id == vid).first()
                    if not video:
                        continue
                    if owner_id is not None and getattr(video, "owner_id", None) != owner_id:
                        continue
                    results.append(
                        VideoSearchResult(
                            video_id=video.id,
                            title=video.title,
                            path=getattr(video, "path", None) or getattr(video, "url", None),
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

    return await run_in_threadpool(_sync_search)

# Upload video file
@router.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    title: str = Form(...),
    owner_id: int | None = Form(None),
    db: Session = Depends(get_db)
):
    # Validate MIME type
    content_type = file.content_type or ""
    if not content_type.startswith("video/"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file type '{content_type}'. Only video files are allowed.",
        )

    def _sync_upload_video():
        # Generate unique filename preserving extension
        original_ext = os.path.splitext(file.filename)[1]
        unique_name = f"{uuid.uuid4().hex}{original_ext}"
        save_path = os.path.join(VIDEO_DIR, unique_name)

        # Persist a file to disk
        with open(save_path, "wb") as out_file:
            out_file.write(file.file.read())

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

        return {"task_id": async_result.id, "status": "processing", "video_id": db_video.id}

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

# List videos
@router.get("/", response_model=List[VideoRead])
async def list_videos(skip: int = 0, limit: int = 100, owner_id: int | None = None, db: Session = Depends(get_db)):
    def _sync_list_videos():
        query = db.query(models.Video)
        if owner_id is not None:
            query = query.filter(models.Video.owner_id == owner_id)
        videos = query.offset(skip).limit(limit).all()
        return videos

    return await run_in_threadpool(_sync_list_videos)

# Get a single video
@router.get("/{video_id}", response_model=VideoRead)
async def get_video(video_id: int, db: Session = Depends(get_db)):
    def _sync_get_video():
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found")
        return video

    return await run_in_threadpool(_sync_get_video)

# Delete video
@router.delete("/{video_id}")
async def delete_video(video_id: int, db: Session = Depends(get_db)):
    def _sync_delete_video():
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found")

        # Attempt to remove an associated file if a path present
        if getattr(video, "path", None):
            # Build absolute path inside the expected directory
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

    return await run_in_threadpool(_sync_delete_video)


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