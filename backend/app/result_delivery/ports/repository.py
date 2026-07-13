from datetime import datetime
from typing import Protocol

from app.result_delivery.domain.event import ProcessingResultEvent
from app.result_delivery.domain.failure_classification import PublicationFailureClassification


class ProcessingResultOutboxRepository(Protocol):
    def append(self, event: ProcessingResultEvent) -> ProcessingResultEvent:
        ...

    def select_due_event_ids(self, *, now: datetime, limit: int) -> tuple[str, ...]:
        ...

    def claim(self, event_id: str, *, now: datetime) -> ProcessingResultEvent | None:
        ...

    def finalize_published(self, event_id: str, *, now: datetime) -> bool:
        ...

    def record_publication_failure(
        self,
        event_id: str,
        *,
        classification: PublicationFailureClassification,
        now: datetime,
        max_attempts: int,
        retry_delay_seconds: int,
        recovery_max_cycles: int,
        recovery_cooldown_seconds: int,
    ) -> bool | None:
        """Return True for retry, False for terminal failure, and None for lost claim."""
        ...

    def select_recovery_event_ids(
        self,
        *,
        now: datetime,
        limit: int,
        max_cycles: int,
    ) -> tuple[str, ...]:
        ...

    def requeue_failed(self, event_id: str, *, now: datetime, max_cycles: int) -> bool:
        ...
