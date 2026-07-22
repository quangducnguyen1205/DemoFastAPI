import math
import tempfile
import time
from numbers import Real
from typing import Any

from app.processing.domain.models import ProcessingExecutionCommand, ProcessingTranscriptRow
from app.services.video_processing import extract_audio_to_wav, segment_text, transcribe_audio_with_whisper
from app.processing.adapters.timing import log_processing_timing


def seconds_to_milliseconds(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("Whisper segment timestamp must be a finite number")
    seconds = float(value)
    if not math.isfinite(seconds):
        raise ValueError("Whisper segment timestamp must be finite")
    if seconds < 0:
        raise ValueError("Whisper segment timestamp must not be negative")
    return round(seconds * 1000)


def normalize_whisper_result(result: dict[str, Any] | None) -> tuple[ProcessingTranscriptRow, ...]:
    if result is None:
        return ()

    raw_segments = result.get("segments")
    if raw_segments is None or raw_segments == []:
        full_text = str(result.get("text") or "").strip()
        return tuple(
            ProcessingTranscriptRow(segment_index=index, text=text)
            for index, text in enumerate(segment_text(full_text) if full_text else ())
        )
    if not isinstance(raw_segments, list):
        raise ValueError("Whisper segments must be a list")

    rows: list[ProcessingTranscriptRow] = []
    for segment_index, raw_segment in enumerate(raw_segments):
        if not isinstance(raw_segment, dict):
            raise ValueError("Whisper segment must be an object")
        text = str(raw_segment.get("text") or "").strip()
        if not text:
            raise ValueError("Whisper segment text must not be empty")
        start_ms = seconds_to_milliseconds(raw_segment.get("start"))
        end_ms = seconds_to_milliseconds(raw_segment.get("end"))
        if (start_ms is None) != (end_ms is None):
            raise ValueError("Whisper segment timestamps must both be present or both be absent")
        if start_ms is not None and end_ms < start_ms:
            raise ValueError("Whisper segment end timestamp must not precede start timestamp")
        rows.append(ProcessingTranscriptRow(segment_index, text, start_ms, end_ms))
    return tuple(rows)


class WhisperProcessingTranscriptionProvider:
    def transcribe(
        self,
        media_path: str,
        *,
        command: ProcessingExecutionCommand | None = None,
        task_id: str | None = None,
        video_id: int | None = None,
    ) -> tuple[ProcessingTranscriptRow, ...]:
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
            result = transcribe_audio_with_whisper(audio_path)
            log_processing_timing(
                "whisper_ms",
                (time.perf_counter() - started_at) * 1000,
                task_id=task_id,
                video_id=video_id,
                asset_id=asset_id,
            )

        started_at = time.perf_counter()
        segments = normalize_whisper_result(result)
        log_processing_timing(
            "chunking_ms",
            (time.perf_counter() - started_at) * 1000,
            task_id=task_id,
            video_id=video_id,
            asset_id=asset_id,
            segment_count=len(segments),
        )
        return segments
