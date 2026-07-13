from dataclasses import dataclass
from enum import Enum

from app.result_delivery.domain.failures import (
    PermanentProcessingResultPublisherError,
    TransientProcessingResultPublisherError,
)


class PublicationFailureDisposition(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"
    RECOVERY_EXHAUSTED = "recovery_exhausted"


@dataclass(frozen=True)
class PublicationFailureClassification:
    disposition: PublicationFailureDisposition
    safe_category: str


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
    if isinstance(exc, PermanentProcessingResultPublisherError):
        return PublicationFailureClassification(
            PublicationFailureDisposition.PERMANENT,
            "invalid_outbox_event",
        )
    if isinstance(exc, (TypeError, ValueError)):
        return PublicationFailureClassification(
            PublicationFailureDisposition.PERMANENT,
            "serialization_or_configuration_failure",
        )
    return None


def _classify_transient(exc: BaseException) -> PublicationFailureClassification | None:
    if isinstance(exc, TransientProcessingResultPublisherError):
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
