from dataclasses import dataclass
from datetime import datetime

from app.result_delivery.domain.failure_classification import PublicationFailureDisposition


@dataclass(frozen=True)
class ProcessingOutboxState:
    status: str
    failure_disposition: str | None
    next_recovery_at: datetime | None
    recovery_cycle_count: int


def is_recovery_eligible(
    state: ProcessingOutboxState,
    *,
    now: datetime,
    max_cycles: int,
) -> bool:
    return (
        state.status == "failed"
        and state.failure_disposition == PublicationFailureDisposition.TRANSIENT.value
        and state.next_recovery_at is not None
        and state.next_recovery_at <= now
        and state.recovery_cycle_count < max_cycles
    )
