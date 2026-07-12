from dataclasses import dataclass
from enum import Enum

from app.services.processing_outbox_publisher import (
    PermanentProcessingOutboxPublisherError,
)

try:
    from kafka import errors as kafka_errors
except ModuleNotFoundError:  # unit-test/editor environments may not install runtime Kafka extras
    kafka_errors = None


class PublicationFailureDisposition(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"
    RECOVERY_EXHAUSTED = "recovery_exhausted"


@dataclass(frozen=True)
class PublicationFailureClassification:
    disposition: PublicationFailureDisposition
    safe_category: str


def _kafka_error_types(*names: str) -> tuple[type[BaseException], ...]:
    if kafka_errors is None:
        return ()
    return tuple(
        error_type
        for name in names
        if isinstance((error_type := getattr(kafka_errors, name, None)), type)
    )


_KAFKA_ERROR_TYPES = _kafka_error_types("KafkaError")
_KAFKA_TRANSIENT_TYPES = _kafka_error_types(
    "NoBrokersAvailable",
    "BrokerNotAvailableError",
    "KafkaConnectionError",
    "KafkaTimeoutError",
    "NetworkExceptionError",
    "RequestTimedOutError",
    "NodeNotReadyError",
)
_KAFKA_PERMANENT_TYPES = _kafka_error_types(
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


def classify_publication_failure(exc: BaseException | None) -> PublicationFailureClassification:
    visited: set[int] = set()
    current = exc
    transient: PublicationFailureClassification | None = None

    while current is not None and len(visited) < 32 and id(current) not in visited:
        visited.add(id(current))
        permanent = _classify_permanent(current)
        if permanent is not None:
            return permanent
        if transient is None:
            transient = _classify_transient(current)
        current = current.__cause__ or current.__context__

    return transient or PublicationFailureClassification(
        PublicationFailureDisposition.UNKNOWN,
        "unknown_publication_failure",
    )


def _classify_permanent(exc: BaseException) -> PublicationFailureClassification | None:
    if isinstance(exc, PermanentProcessingOutboxPublisherError):
        return PublicationFailureClassification(
            PublicationFailureDisposition.PERMANENT,
            "invalid_outbox_event",
        )
    if _KAFKA_PERMANENT_TYPES and isinstance(exc, _KAFKA_PERMANENT_TYPES):
        return PublicationFailureClassification(
            PublicationFailureDisposition.PERMANENT,
            "kafka_configuration_failure",
        )
    if isinstance(exc, (TypeError, ValueError)):
        return PublicationFailureClassification(
            PublicationFailureDisposition.PERMANENT,
            "serialization_or_configuration_failure",
        )
    return None


def _classify_transient(exc: BaseException) -> PublicationFailureClassification | None:
    if _KAFKA_TRANSIENT_TYPES and isinstance(exc, _KAFKA_TRANSIENT_TYPES):
        return PublicationFailureClassification(
            PublicationFailureDisposition.TRANSIENT,
            "kafka_retryable_failure",
        )
    if _KAFKA_ERROR_TYPES and isinstance(exc, _KAFKA_ERROR_TYPES) and bool(getattr(exc, "retriable", False)):
        return PublicationFailureClassification(
            PublicationFailureDisposition.TRANSIENT,
            "kafka_retryable_failure",
        )
    if isinstance(exc, TimeoutError):
        return PublicationFailureClassification(
            PublicationFailureDisposition.TRANSIENT,
            "publication_timeout",
        )
    if isinstance(exc, ConnectionError):
        return PublicationFailureClassification(
            PublicationFailureDisposition.TRANSIENT,
            "broker_connection_failure",
        )
    return None
