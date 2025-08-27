import os
import logging
import subprocess
import tempfile
from typing import List

from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .celery import celery_app
from . import models


def split_transcript_text(text: str, max_len: int = 200) -> List[str]:
    if not text:
        return []
    import re
    parts = re.split(r"([.!?])", text)
    sentences: List[str] = []
    for i in range(0, len(parts), 2):
        sent = parts[i].strip()
        if not sent:
            continue
        delim = parts[i + 1] if i + 1 < len(parts) else ""
        sentences.append((sent + delim).strip())
    chunks: List[str] = []
    current = ""
    for s in sentences:
        if not current:
            current = s
        elif len(current) + 1 + len(s) <= max_len:
            current = f"{current} {s}"
        else:
            chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    wrapped: List[str] = []
    for ch in chunks:
        if len(ch) <= max_len:
            wrapped.append(ch)
        else:
            for i in range(0, len(ch), max_len):
                wrapped.append(ch[i:i+max_len])
    return wrapped


def _get_db_session() -> Session:
    # Build a minimal session factory here to avoid import cycles
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/userdb")
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()


@celery_app.task(name="process_video")
def process_video_task(video_id: int, abs_video_path: str) -> dict:
    """Extract audio, transcribe with whisper, store transcript segments,
    and update FAISS index.
    """
    db = _get_db_session()
    try:
        video = db.query(models.Video).filter(models.Video.id == video_id).first()
        if not video:
            return {"status": "failed", "error": f"Video {video_id} not found"}

        transcript_segments: List[str] = []
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = os.path.join(tmpdir, "audio.wav")
                ffmpeg_cmd = [
                    "ffmpeg", "-y", "-i", abs_video_path,
                    "-vn", "-ac", "1", "-ar", "16000", audio_path
                ]
                subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                try:
                    import whisper  # lazy import inside worker
                    model = whisper.load_model("base")
                    result = model.transcribe(audio_path)
                    full_text = result.get("text", "").strip() or None
                    if full_text:
                        transcript_segments = split_transcript_text(full_text, max_len=200)
                except Exception as whisper_err:
                    logging.warning(f"Whisper transcription failed: {whisper_err}")

            # Store transcript segments
            if transcript_segments:
                for idx, seg in enumerate(transcript_segments):
                    db.add(models.Transcript(video_id=video.id, segment_index=idx, text=seg))
                db.commit()

                # Embeddings + FAISS
                import numpy as np
                embeddings = [models.generate_embedding(seg) for seg in transcript_segments]
                vecs = np.array(embeddings, dtype='float32')
                dim = vecs.shape[1]
                index = models.load_faiss_index(dim)
                import faiss  # type: ignore
                index.add(vecs)
                models.save_faiss_index(index)
                mapping = models.load_faiss_mapping()
                start_id = index.ntotal - len(transcript_segments)
                for offset in range(len(transcript_segments)):
                    mapping[start_id + offset] = video.id
                models.save_faiss_mapping(mapping)

            # Mark video as ready
            video.status = "ready"
            db.commit()
            return {"status": "ready", "segments": transcript_segments}
        except Exception as e:
            logging.exception("Processing failed")
            video.status = "failed"
            db.commit()
            return {"status": "failed", "error": str(e)}
    finally:
        db.close()
