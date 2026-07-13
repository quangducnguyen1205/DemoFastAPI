"""Compatibility entrypoints for feature-owned processing-result recording."""

from datetime import UTC, datetime

from app.processing.domain.models import (
    ProcessingArtifact,
    ProcessingFailed,
    ProcessingFailure,
    ProcessingSucceeded,
    ProcessingTranscriptRow,
)
from app.result_delivery.adapters.sqlalchemy_repository import SqlAlchemyProcessingResultOutboxRepository
from app.result_delivery.application.record_result import (
    ASSET_PROCESSING_FAILED_EVENT_TYPE,
    MAX_SAFE_ERROR_MESSAGE_LENGTH,
    PROCESSING_RESULT_AGGREGATE_TYPE,
    PROCESSING_RESULT_EVENT_VERSION,
    TRANSCRIPT_READY_EVENT_TYPE,
    RecordProcessingResultApplicationService,
    safe_error_message,
)

DEFAULT_PROCESSING_ERROR_CODE = "PROCESSING_FAILED"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def add_transcript_ready_event(db, *, processing_request, segment_count: int, completed_at=None):
    completed_at = completed_at or _utc_now()
    artifact = ProcessingArtifact(
        tuple(ProcessingTranscriptRow(index, "") for index in range(segment_count))
    )
    return RecordProcessingResultApplicationService(
        SqlAlchemyProcessingResultOutboxRepository(db)
    ).record(
        ProcessingSucceeded(
            processing_request.event_id,
            processing_request.asset_id,
            artifact,
            completed_at,
        )
    )


def add_processing_failed_event(db, *, processing_request, exc: Exception, completed_at=None):
    completed_at = completed_at or _utc_now()
    return RecordProcessingResultApplicationService(
        SqlAlchemyProcessingResultOutboxRepository(db)
    ).record(
        ProcessingFailed(
            processing_request.event_id,
            processing_request.asset_id,
            ProcessingFailure(DEFAULT_PROCESSING_ERROR_CODE, str(exc), exc),
            completed_at,
        )
    )
