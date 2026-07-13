from typing import Protocol

from app.processing.domain.models import ProcessingExecutionCommand


class ProcessingTranscriptionProvider(Protocol):
    def transcribe(
        self,
        media_path: str,
        *,
        command: ProcessingExecutionCommand | None = None,
        task_id: str | None = None,
        video_id: int | None = None,
    ) -> tuple[str, ...]:
        ...
