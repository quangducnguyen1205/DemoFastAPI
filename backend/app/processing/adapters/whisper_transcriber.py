import tempfile
import time

from app.processing.domain.models import ProcessingExecutionCommand
from app.services.video_processing import extract_audio_to_wav, segment_text, transcribe_audio_with_whisper
from app.processing.adapters.timing import log_processing_timing


class WhisperProcessingTranscriptionProvider:
    def transcribe(
        self,
        media_path: str,
        *,
        command: ProcessingExecutionCommand | None = None,
        task_id: str | None = None,
        video_id: int | None = None,
    ) -> tuple[str, ...]:
        asset_id = command.asset_id if command else None
        with tempfile.TemporaryDirectory(prefix="vp_") as temp_dir:
            started_at = time.perf_counter()
            audio_path = extract_audio_to_wav(media_path, temp_dir=temp_dir)
            log_processing_timing(
                "ffmpeg_ms",
                (time.perf_counter() - started_at) * 1000,
                task_id=task_id,
                video_id=video_id,
                asset_id=asset_id,
            )
            started_at = time.perf_counter()
            full_text = transcribe_audio_with_whisper(audio_path)
            log_processing_timing(
                "whisper_ms",
                (time.perf_counter() - started_at) * 1000,
                task_id=task_id,
                video_id=video_id,
                asset_id=asset_id,
            )

        started_at = time.perf_counter()
        segments = tuple(segment_text(full_text) if full_text else ())
        log_processing_timing(
            "chunking_ms",
            (time.perf_counter() - started_at) * 1000,
            task_id=task_id,
            video_id=video_id,
            asset_id=asset_id,
            segment_count=len(segments),
        )
        return segments
