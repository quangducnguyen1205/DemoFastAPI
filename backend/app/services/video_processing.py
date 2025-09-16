import os
import subprocess
import tempfile
import logging
from typing import List

from sqlalchemy.orm import Session

from app import models
from . import semantic_index
from app.utils import split_transcript_text


def extract_audio_to_wav(abs_video_path: str, sample_rate: int = 16000) -> str:
    """Extract mono WAV audio from a video to a temp file and return the path."""
    tmpdir = tempfile.mkdtemp(prefix="vp_")
    audio_path = os.path.join(tmpdir, "audio.wav")
    cmd = [
        "ffmpeg", "-y", "-i", abs_video_path,
        "-vn", "-ac", "1", "-ar", str(sample_rate), audio_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return audio_path


def transcribe_audio_with_whisper(audio_path: str) -> str | None:
    """Transcribe audio using Whisper (base model). Returns full text or None."""
    try:
        import whisper  # heavy import; keep inside a worker process
        model = whisper.load_model("base")
        result = model.transcribe(audio_path)
        return (result.get("text", "") or "").strip() or None
    except Exception as e:
        logging.warning(f"Whisper transcription failed: {e}")
        return None


def segment_text(full_text: str, max_len: int = 200) -> List[str]:
    return split_transcript_text(full_text, max_len=max_len)


def persist_transcript_segments(db: Session, video_id: int, segments: List[str]) -> None:
    for idx, seg in enumerate(segments):
        db.add(models.Transcript(video_id=video_id, segment_index=idx, text=seg))
    db.commit()


def embed_and_update_faiss(segments: List[str], video_id: int) -> None:
    import numpy as np
    import faiss  # type: ignore

    if not segments:
        return
    embeddings = [semantic_index.generate_embedding(seg) for seg in segments]
    vecs = np.array(embeddings, dtype="float32")
    dim = vecs.shape[1]
    index = semantic_index.load_faiss_index(dim)
    # add vectors to the index
    index.add(vecs)
    semantic_index.save_faiss_index(index)
    # update mapping
    mapping = semantic_index.load_faiss_mapping()
    start_id = index.ntotal - len(segments)
    for offset in range(len(segments)):
        mapping[start_id + offset] = video_id
    semantic_index.save_faiss_mapping(mapping)

