import logging
import os
import subprocess
import threading
from typing import Any, List
from app.config.settings import settings
from app.processing.domain.failures import MediaTranscodingTimeoutError
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


def extract_audio_to_wav(
    abs_video_path: str,
    temp_dir: str,
    sample_rate: int = 16000,
    *,
    timeout_seconds: float | None = None,
) -> str:
    """Extract mono WAV audio from a video to a temp file and return the path.

    Bounded: a malformed or pathological input cannot leave ffmpeg running. On expiry
    ``subprocess.run`` kills the child and reaps it before raising, so nothing is orphaned, and the
    partly written WAV goes away with the caller's temporary directory.
    """
    audio_path = os.path.join(temp_dir, "audio.wav")
    cmd = [
        "ffmpeg", "-y", "-i", abs_video_path,
        "-vn", "-ac", "1", "-ar", str(sample_rate), audio_path
    ]
    bound = settings.FFMPEG_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=bound,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "audio extraction timed out failure_family=%s timeout_seconds=%g",
            MediaTranscodingTimeoutError.diagnostic_family,
            bound,
        )
        raise MediaTranscodingTimeoutError(bound) from exc
    return audio_path


def transcribe_audio_with_whisper(audio_path: str) -> dict[str, Any]:
    """Transcribe audio using Whisper (base model). Raises if transcription cannot be completed."""
    model = get_whisper_model()
    result = model.transcribe(audio_path)
    if not isinstance(result, dict):
        raise ValueError("Whisper returned a non-object transcription result")
    return result


def segment_text(full_text: str, max_len: int = DEFAULT_TRANSCRIPT_CHUNK_CHARS) -> List[str]:
    return split_transcript_text(full_text, max_len=max_len)
