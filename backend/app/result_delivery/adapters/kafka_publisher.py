import json
import logging
from typing import Any

from app.config.settings import settings
from app.result_delivery.adapters.event_codec import ProcessingResultEventCodec
from app.result_delivery.domain.event import ProcessingResultEvent
from app.result_delivery.domain.failures import (
    PermanentProcessingResultPublisherError,
    ProcessingResultPublisherDisabledError,
    ProcessingResultPublisherError,
    TransientProcessingResultPublisherError,
)

logger = logging.getLogger(__name__)


class DisabledProcessingResultPublisher:
    def publish(self, event: ProcessingResultEvent) -> None:
        raise ProcessingResultPublisherDisabledError(
            "processing result publisher is disabled; set PROCESSING_RESULT_PUBLISHER_ENABLED=true"
        )


class KafkaProcessingResultPublisher:
    def __init__(
        self,
        *,
        topic: str | None = None,
        bootstrap_servers: list[str] | None = None,
        send_timeout_seconds: float | None = None,
        codec: ProcessingResultEventCodec | None = None,
    ) -> None:
        self.topic = topic or settings.KAFKA_PROCESSING_RESULT_TOPIC
        self.bootstrap_servers = bootstrap_servers or settings.KAFKA_BOOTSTRAP_SERVERS_LIST
        self.send_timeout_seconds = send_timeout_seconds or settings.KAFKA_SEND_TIMEOUT_SECONDS
        self._codec = codec or ProcessingResultEventCodec()
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

    def publish(self, event: ProcessingResultEvent) -> None:
        envelope = self._codec.encode(event)
        try:
            future = self._get_producer().send(self.topic, key=event.event_key, value=envelope)
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
            translated = _translate_transport_failure(exc)
            raise translated(
                f"failed to publish processing outbox event event_id={event.id} topic={self.topic}: {exc}"
            ) from exc

    def close(self) -> None:
        if self._producer is not None:
            self._producer.close(timeout=self.send_timeout_seconds)
            self._producer = None


def _translate_transport_failure(exc: Exception) -> type[ProcessingResultPublisherError]:
    try:
        from kafka import errors as kafka_errors
    except ModuleNotFoundError:
        kafka_errors = None
    if kafka_errors is not None:
        permanent_names = (
            "SerializationError",
            "InvalidTopicError",
            "MessageSizeTooLargeError",
            "RecordTooLargeError",
            "UnsupportedVersionError",
            "AuthenticationFailedError",
            "TopicAuthorizationFailedError",
            "GroupAuthorizationFailedError",
            "ClusterAuthorizationFailedError",
            "KafkaConfigurationError",
            "UnsupportedCodecError",
        )
        transient_names = (
            "NoBrokersAvailable",
            "BrokerNotAvailableError",
            "KafkaConnectionError",
            "KafkaTimeoutError",
            "NetworkExceptionError",
            "RequestTimedOutError",
            "NodeNotReadyError",
        )
        permanent = tuple(
            value for name in permanent_names if isinstance((value := getattr(kafka_errors, name, None)), type)
        )
        transient = tuple(
            value for name in transient_names if isinstance((value := getattr(kafka_errors, name, None)), type)
        )
        if permanent and isinstance(exc, permanent):
            return PermanentProcessingResultPublisherError
        if transient and isinstance(exc, transient):
            return TransientProcessingResultPublisherError
        kafka_error = getattr(kafka_errors, "KafkaError", None)
        if isinstance(kafka_error, type) and isinstance(exc, kafka_error) and bool(getattr(exc, "retriable", False)):
            return TransientProcessingResultPublisherError
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return TransientProcessingResultPublisherError
    if isinstance(exc, (TypeError, ValueError)):
        return PermanentProcessingResultPublisherError
    return ProcessingResultPublisherError


def build_processing_result_publisher():
    if not settings.PROCESSING_RESULT_PUBLISHER_ENABLED:
        return DisabledProcessingResultPublisher()
    return KafkaProcessingResultPublisher()
