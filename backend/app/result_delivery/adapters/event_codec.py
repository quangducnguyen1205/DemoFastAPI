from datetime import UTC, datetime
from typing import Any

from app.result_delivery.domain.event import ProcessingResultEvent
from app.result_delivery.domain.failures import PermanentProcessingResultPublisherError


def _isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ProcessingResultEventCodec:
    _ALLOWED_KEYS = {
        "transcript.ready": {
            "assetId",
            "processingRequestId",
            "status",
            "segmentCount",
            "completedAt",
        },
        "asset.processing.failed": {
            "assetId",
            "processingRequestId",
            "status",
            "errorCode",
            "errorMessage",
            "completedAt",
        },
    }

    def encode(self, event: ProcessingResultEvent) -> dict[str, Any]:
        payload = self._assert_supported_payload(event)
        return {
            "eventId": event.id,
            "eventType": event.event_type,
            "eventVersion": event.event_version,
            "aggregateType": event.aggregate_type,
            "aggregateId": event.aggregate_id,
            "eventKey": event.event_key,
            "causationEventId": event.causation_event_id,
            "occurredAt": _isoformat(event.occurred_at),
            "payload": payload,
        }

    def _assert_supported_payload(self, event: ProcessingResultEvent) -> dict[str, Any]:
        if not isinstance(event.payload, dict):
            raise PermanentProcessingResultPublisherError(
                f"outbox event payload must be a JSON object event_id={event.id} event_type={event.event_type}"
            )
        allowed_keys = self._ALLOWED_KEYS.get(event.event_type)
        if allowed_keys is None:
            raise PermanentProcessingResultPublisherError(
                f"unsupported outbox event_type={event.event_type}"
            )
        extra_keys = set(event.payload) - allowed_keys
        if extra_keys:
            raise PermanentProcessingResultPublisherError(
                f"outbox event payload contains unsupported keys event_id={event.id} keys={sorted(extra_keys)}"
            )
        return event.payload
