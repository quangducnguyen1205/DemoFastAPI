import os
import subprocess
import logging
import threading
from typing import List

from sqlalchemy.orm import Session

from app import models
from app.services.semantic_index import generate_embeddings
from app.services.semantic_index.writer import (
    load_or_create_index,
    add_embeddings,
    save_index,
)
from app.utils import DEFAULT_TRANSCRIPT_CHUNK_CHARS, split_transcript_text

logger = logging.getLogger(__name__)
_whisper_model = None
_whisper_model_lock = threading.Lock()


def get_whisper_model(model_name: str = "base"):
    global _whisper_model
    if _whisper_model is None:
        with _whisper_model_lock:
            if _whisper_model is None:
                import whisper  # heavy import; keep inside a worker process
                _whisper_model = whisper.load_model(model_name)
    return _whisper_model


def extract_audio_to_wav(abs_video_path: str, temp_dir: str, sample_rate: int = 16000) -> str:
    """Extract mono WAV audio from a video to a temp file and return the path."""
    audio_path = os.path.join(temp_dir, "audio.wav")
    cmd = [
        "ffmpeg", "-y", "-i", abs_video_path,
        "-vn", "-ac", "1", "-ar", str(sample_rate), audio_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return audio_path


def transcribe_audio_with_whisper(audio_path: str) -> str | None:
    """Transcribe audio using Whisper (base model). Returns full text or None."""
    try:
        model = get_whisper_model()
        result = model.transcribe(audio_path)
        return (result.get("text", "") or "").strip() or None
    except Exception as e:
        logger.warning("Whisper transcription failed: %s", e)
        return None


def segment_text(full_text: str, max_len: int = DEFAULT_TRANSCRIPT_CHUNK_CHARS) -> List[str]:
    return split_transcript_text(full_text, max_len=max_len)


def persist_transcript_segments(db: Session, video_id: int, segments: List[str]) -> None:
    for idx, seg in enumerate(segments):
        db.add(models.Transcript(video_id=video_id, segment_index=idx, text=seg))
    db.commit()


def embed_and_update_faiss(segments: List[str], video_id: int) -> None:
    import numpy as np

    if not segments:
        return
    vecs = np.array(generate_embeddings(segments), dtype="float32")
    dim = vecs.shape[1]
    load_or_create_index(dim)
    add_embeddings(vecs, [video_id] * len(segments))
    save_index()
