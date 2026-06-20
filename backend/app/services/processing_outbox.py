import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app import models


TRANSCRIPT_READY_EVENT_TYPE = "transcript.ready"
ASSET_PROCESSING_FAILED_EVENT_TYPE = "asset.processing.failed"
PROCESSING_RESULT_EVENT_VERSION = 1
PROCESSING_RESULT_AGGREGATE_TYPE = "ASSET"
DEFAULT_PROCESSING_ERROR_CODE = "PROCESSING_FAILED"
MAX_SAFE_ERROR_MESSAGE_LENGTH = 500

_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(password|secret|token|access[_-]?key|credential)(\s*[=:]\s*)([^\s,;]+)"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def safe_error_message(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").replace("\r", " ").strip()
    message = _SENSITIVE_VALUE_PATTERN.sub(r"\1\2[redacted]", message)
    if not message:
        message = exc.__class__.__name__
    if len(message) > MAX_SAFE_ERROR_MESSAGE_LENGTH:
        message = message[: MAX_SAFE_ERROR_MESSAGE_LENGTH - 3].rstrip() + "..."
    return message


def _create_event(
    db: Session,
    *,
    processing_request: models.ProcessingRequest,
    event_type: str,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> models.ProcessingOutboxEvent:
    existing = db.query(models.ProcessingOutboxEvent).filter(
        models.ProcessingOutboxEvent.causation_event_id == processing_request.event_id,
        models.ProcessingOutboxEvent.event_type == event_type,
    ).first()
    if existing:
        return existing

    event = models.ProcessingOutboxEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,
        event_version=PROCESSING_RESULT_EVENT_VERSION,
        aggregate_type=PROCESSING_RESULT_AGGREGATE_TYPE,
        aggregate_id=processing_request.asset_id,
        event_key=processing_request.asset_id,
        causation_event_id=processing_request.event_id,
        occurred_at=occurred_at,
        payload=payload,
        status="pending",
        attempt_count=0,
    )
    db.add(event)
    return event


def add_transcript_ready_event(
    db: Session,
    *,
    processing_request: models.ProcessingRequest,
    segment_count: int,
    completed_at: datetime | None = None,
) -> models.ProcessingOutboxEvent:
    completed_at = completed_at or _utc_now()
    payload = {
        "assetId": processing_request.asset_id,
        "processingRequestId": processing_request.event_id,
        "status": "ready",
        "segmentCount": segment_count,
        "completedAt": _isoformat(completed_at),
    }
    return _create_event(
        db,
        processing_request=processing_request,
        event_type=TRANSCRIPT_READY_EVENT_TYPE,
        occurred_at=completed_at,
        payload=payload,
    )


def add_processing_failed_event(
    db: Session,
    *,
    processing_request: models.ProcessingRequest,
    exc: Exception,
    completed_at: datetime | None = None,
) -> models.ProcessingOutboxEvent:
    completed_at = completed_at or _utc_now()
    payload = {
        "assetId": processing_request.asset_id,
        "processingRequestId": processing_request.event_id,
        "status": "failed",
        "errorCode": DEFAULT_PROCESSING_ERROR_CODE,
        "errorMessage": safe_error_message(exc),
        "completedAt": _isoformat(completed_at),
    }
    return _create_event(
        db,
        processing_request=processing_request,
        event_type=ASSET_PROCESSING_FAILED_EVENT_TYPE,
        occurred_at=completed_at,
        payload=payload,
    )
