from datetime import UTC, datetime
import logging

from app.processing.domain.models import (
    ProcessingArtifact,
    ProcessingExecutionCommand,
    ProcessingFailed,
    ProcessingFailure,
    ProcessingSkipped,
    ProcessingSucceeded,
    ProcessingTranscriptRow,
)
from app.processing.ports.artifact_store import DirectUploadArtifactStore, ProcessingArtifactStore
from app.processing.ports.media_source import ProcessingMediaSource
from app.processing.ports.result_sink import ProcessingResultSink
from app.processing.ports.transcription import ProcessingTranscriptionProvider

logger = logging.getLogger(__name__)
DEFAULT_PROCESSING_ERROR_CODE = "PROCESSING_FAILED"


class ExecuteProcessingApplicationService:
    def __init__(
        self,
        *,
        media_source: ProcessingMediaSource,
        transcriber: ProcessingTranscriptionProvider,
        artifact_store: ProcessingArtifactStore,
        result_sink: ProcessingResultSink,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._media_source = media_source
        self._transcriber = transcriber
        self._artifact_store = artifact_store
        self._result_sink = result_sink
        self._clock = clock

    def execute(self, command: ProcessingExecutionCommand, *, task_id: str | None = None):
        existing_status = self._artifact_store.claim(command)
        if existing_status is not None:
            logger.info(
                "skipping duplicate asset object task event_id=%s asset_id=%s status=%s",
                command.event_id,
                command.asset_id,
                existing_status,
            )
            return ProcessingSkipped(command.event_id, command.asset_id, existing_status)

        try:
            with self._media_source.acquire(command) as media_path:
                segments = self._transcriber.transcribe(media_path, command=command, task_id=task_id)
            artifact = ProcessingArtifact(
                tuple(
                    ProcessingTranscriptRow(segment_index=index, text=segment)
                    for index, segment in enumerate(segments)
                )
            )
            outcome = ProcessingSucceeded(command.event_id, command.asset_id, artifact, self._clock())
            self._artifact_store.persist_success(outcome)
            self._result_sink.record(outcome)
            self._artifact_store.commit()
            return outcome
        except Exception as exc:
            logger.exception(
                "Asset object processing failed event_id=%s asset_id=%s",
                command.event_id,
                command.asset_id,
            )
            self._artifact_store.rollback()
            outcome = ProcessingFailed(
                command.event_id,
                command.asset_id,
                ProcessingFailure(DEFAULT_PROCESSING_ERROR_CODE, str(exc), exc),
                self._clock(),
            )
            self._artifact_store.persist_failure(outcome)
            self._result_sink.record(outcome)
            self._artifact_store.commit()
            return outcome

    def close(self) -> None:
        self._artifact_store.close()


class ExecuteDirectUploadProcessingApplicationService:
    def __init__(
        self,
        *,
        transcriber: ProcessingTranscriptionProvider,
        artifact_store: DirectUploadArtifactStore,
    ) -> None:
        self._transcriber = transcriber
        self._artifact_store = artifact_store

    def execute(
        self,
        *,
        video_id: int,
        media_path: str,
        task_id: str | None = None,
    ) -> dict:
        if not self._artifact_store.exists(video_id):
            return {"status": "failed", "error": f"Video {video_id} not found"}
        try:
            segments = self._transcriber.transcribe(media_path, task_id=task_id, video_id=video_id)
            self._artifact_store.persist_ready(video_id, segments)
            return {"status": "ready", "segments": list(segments)}
        except Exception as exc:
            logger.exception("Processing failed")
            self._artifact_store.persist_failed(video_id)
            return {"status": "failed", "error": str(exc)}

    def close(self) -> None:
        self._artifact_store.close()
