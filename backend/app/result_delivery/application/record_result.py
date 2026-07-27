import uuid

from app.processing.domain.models import ProcessingFailed, ProcessingOutcome, ProcessingSucceeded
from app.processing.domain.failures import safe_processing_error_message
from app.result_delivery.domain.event import ProcessingResultEvent
from app.result_delivery.ports.repository import ProcessingResultOutboxRepository

TRANSCRIPT_READY_EVENT_TYPE = "transcript.ready"
ASSET_PROCESSING_FAILED_EVENT_TYPE = "asset.processing.failed"
PROCESSING_RESULT_EVENT_VERSION = 1
PROCESSING_RESULT_AGGREGATE_TYPE = "ASSET"


def isoformat_utc(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def safe_error_message(exc: Exception) -> str:
    return safe_processing_error_message(exc)


class RecordProcessingResultApplicationService:
    def __init__(
        self,
        repository: ProcessingResultOutboxRepository,
        *,
        event_id_factory=lambda: str(uuid.uuid4()),
    ) -> None:
        self._repository = repository
        self._event_id_factory = event_id_factory

    def record(self, outcome: ProcessingOutcome) -> ProcessingResultEvent:
        if isinstance(outcome, ProcessingSucceeded):
            event_type = TRANSCRIPT_READY_EVENT_TYPE
            payload = {
                "assetId": outcome.asset_id,
                "processingRequestId": outcome.event_id,
                "status": "ready",
                "segmentCount": outcome.artifact.segment_count,
                "completedAt": isoformat_utc(outcome.completed_at),
            }
        elif isinstance(outcome, ProcessingFailed):
            event_type = ASSET_PROCESSING_FAILED_EVENT_TYPE
            payload = {
                "assetId": outcome.asset_id,
                "processingRequestId": outcome.event_id,
                "status": "failed",
                "errorCode": outcome.failure.code,
                "errorMessage": safe_error_message(outcome.failure.cause),
                "completedAt": isoformat_utc(outcome.completed_at),
            }
        else:  # pragma: no cover - ProcessingOutcome is an exhaustive union
            raise TypeError(f"unsupported processing outcome: {type(outcome).__name__}")

        return self._repository.append(
            ProcessingResultEvent(
                id=self._event_id_factory(),
                event_type=event_type,
                event_version=PROCESSING_RESULT_EVENT_VERSION,
                aggregate_type=PROCESSING_RESULT_AGGREGATE_TYPE,
                aggregate_id=outcome.asset_id,
                event_key=outcome.asset_id,
                causation_event_id=outcome.event_id,
                occurred_at=outcome.completed_at,
                payload=payload,
            )
        )
