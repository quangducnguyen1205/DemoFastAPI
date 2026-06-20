import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from app import models
from app.config.settings import settings

logger = logging.getLogger(__name__)


class ProcessingOutboxPublisherError(RuntimeError):
    pass


class PublisherDisabledError(ProcessingOutboxPublisherError):
    pass


class ProcessingOutboxPublisher(Protocol):
    def publish(self, event: models.ProcessingOutboxEvent) -> None:
        ...


def _isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _assert_supported_payload(event: models.ProcessingOutboxEvent, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProcessingOutboxPublisherError(
            f"outbox event payload must be a JSON object event_id={event.id} event_type={event.event_type}"
        )

    allowed_keys_by_type = {
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
    allowed_keys = allowed_keys_by_type.get(event.event_type)
    if allowed_keys is None:
        raise ProcessingOutboxPublisherError(f"unsupported outbox event_type={event.event_type}")

    extra_keys = set(payload) - allowed_keys
    if extra_keys:
        raise ProcessingOutboxPublisherError(
            f"outbox event payload contains unsupported keys event_id={event.id} keys={sorted(extra_keys)}"
        )
    return payload


def build_result_event_envelope(event: models.ProcessingOutboxEvent) -> dict[str, Any]:
    payload = _assert_supported_payload(event, event.payload)
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


class DisabledProcessingOutboxPublisher:
    def publish(self, event: models.ProcessingOutboxEvent) -> None:
        raise PublisherDisabledError(
            "processing result publisher is disabled; set PROCESSING_RESULT_PUBLISHER_ENABLED=true"
        )


class KafkaProcessingOutboxPublisher:
    def __init__(
        self,
        *,
        topic: str | None = None,
        bootstrap_servers: list[str] | None = None,
        send_timeout_seconds: float | None = None,
    ) -> None:
        self.topic = topic or settings.KAFKA_PROCESSING_RESULT_TOPIC
        self.bootstrap_servers = bootstrap_servers or settings.KAFKA_BOOTSTRAP_SERVERS_LIST
        self.send_timeout_seconds = send_timeout_seconds or settings.KAFKA_SEND_TIMEOUT_SECONDS
        self._producer = None

    def _producer_config(self) -> dict[str, Any]:
        return {
            "bootstrap_servers": self.bootstrap_servers,
            "acks": "all",
            "enable_idempotence": True,
            "key_serializer": lambda value: value.encode("utf-8"),
            "value_serializer": lambda value: json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
        }

    def _get_producer(self):
        if self._producer is None:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(**self._producer_config())
        return self._producer

    def publish(self, event: models.ProcessingOutboxEvent) -> None:
        envelope = build_result_event_envelope(event)
        try:
            future = self._get_producer().send(
                self.topic,
                key=event.event_key,
                value=envelope,
            )
            metadata = future.get(timeout=self.send_timeout_seconds)
            logger.info(
                "published processing outbox event event_id=%s event_type=%s topic=%s partition=%s offset=%s",
                event.id,
                event.event_type,
                metadata.topic,
                metadata.partition,
                metadata.offset,
            )
        except Exception as exc:
            raise ProcessingOutboxPublisherError(
                f"failed to publish processing outbox event event_id={event.id} topic={self.topic}: {exc}"
            ) from exc

    def close(self) -> None:
        if self._producer is not None:
            self._producer.close(timeout=self.send_timeout_seconds)
            self._producer = None


def build_processing_outbox_publisher() -> ProcessingOutboxPublisher:
    if not settings.PROCESSING_RESULT_PUBLISHER_ENABLED:
        return DisabledProcessingOutboxPublisher()
    return KafkaProcessingOutboxPublisher()
