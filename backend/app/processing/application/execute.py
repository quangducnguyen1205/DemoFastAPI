from datetime import UTC, datetime
import logging

from app.processing.domain.models import (
    ProcessingArtifact,
    ProcessingClaimConflict,
    ProcessingExecutionCommand,
    ProcessingFailed,
    ProcessingFailure,
    ProcessingLeaseLost,
    ProcessingSkipped,
    ProcessingSucceeded,
    ProcessingTranscriptRow,
)
from app.processing.domain.failures import safe_processing_error_message
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
        claim = self._artifact_store.claim(command, now=self._clock())
        if isinstance(claim, ProcessingClaimConflict):
            logger.info(
                "skipping duplicate asset object task event_id=%s asset_id=%s status=%s",
                command.event_id,
                command.asset_id,
                claim.status,
            )
            return ProcessingSkipped(
                command.event_id,
                command.asset_id,
                claim.status,
                retry_at=claim.lease_expires_at if claim.status == "processing" else None,
            )

        try:
            with self._media_source.acquire(command) as media_path:
                segments = self._transcriber.transcribe(media_path, command=command, task_id=task_id)
            artifact = ProcessingArtifact(tuple(segments))
            outcome = ProcessingSucceeded(command.event_id, command.asset_id, artifact, self._clock())
            self._artifact_store.persist_success(outcome, attempt_count=claim.attempt_count)
            self._result_sink.record(outcome)
            self._artifact_store.commit()
            return outcome
        except ProcessingLeaseLost:
            self._artifact_store.rollback()
            logger.info(
                "discarding superseded processing completion event_id=%s asset_id=%s attempt_count=%s",
                command.event_id,
                command.asset_id,
                claim.attempt_count,
            )
            return ProcessingSkipped(command.event_id, command.asset_id, "lease_lost")
        except Exception as exc:
            logger.warning(
                "asset object processing failed event_id=%s asset_id=%s failure_type=%s",
                command.event_id,
                command.asset_id,
                type(exc).__name__,
            )
            self._artifact_store.rollback()
            outcome = ProcessingFailed(
                command.event_id,
                command.asset_id,
                ProcessingFailure(
                    DEFAULT_PROCESSING_ERROR_CODE,
                    safe_processing_error_message(exc),
                    exc,
                ),
                self._clock(),
            )
            try:
                self._artifact_store.persist_failure(
                    outcome,
                    attempt_count=claim.attempt_count,
                )
                self._result_sink.record(outcome)
                self._artifact_store.commit()
                return outcome
            except ProcessingLeaseLost:
                self._artifact_store.rollback()
                logger.info(
                    "discarding superseded processing failure event_id=%s asset_id=%s attempt_count=%s",
                    command.event_id,
                    command.asset_id,
                    claim.attempt_count,
                )
                return ProcessingSkipped(command.event_id, command.asset_id, "lease_lost")
            except Exception:
                self._artifact_store.rollback()
                raise

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
            rows = self._transcriber.transcribe(media_path, task_id=task_id, video_id=video_id)
            segments = tuple(row.text for row in rows)
            self._artifact_store.persist_ready(video_id, segments)
            return {"status": "ready", "segments": list(segments)}
        except Exception as exc:
            logger.exception("Processing failed")
            self._artifact_store.persist_failed(video_id)
            return {"status": "failed", "error": str(exc)}

    def close(self) -> None:
        self._artifact_store.close()
