from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app import models
from app.config.settings import settings
from app.services.processing_outbox_failure import PublicationFailureDisposition


@dataclass(frozen=True)
class ProcessingOutboxRecoveryResult:
    eligible: int = 0
    requeued: int = 0
    skipped: int = 0
    disabled: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def is_recovery_eligible(
    event: models.ProcessingOutboxEvent,
    *,
    now: datetime,
    max_cycles: int,
) -> bool:
    return (
        event.status == "failed"
        and event.failure_disposition == PublicationFailureDisposition.TRANSIENT.value
        and event.next_recovery_at is not None
        and event.next_recovery_at <= now
        and (event.recovery_cycle_count or 0) < max_cycles
    )


def _eligible_event_ids(db: Session, now: datetime, batch_size: int, max_cycles: int) -> list[str]:
    rows = (
        db.query(models.ProcessingOutboxEvent.id)
        .filter(models.ProcessingOutboxEvent.status == "failed")
        .filter(models.ProcessingOutboxEvent.failure_disposition == PublicationFailureDisposition.TRANSIENT.value)
        .filter(models.ProcessingOutboxEvent.next_recovery_at.is_not(None))
        .filter(models.ProcessingOutboxEvent.next_recovery_at <= now)
        .filter(models.ProcessingOutboxEvent.recovery_cycle_count < max_cycles)
        .order_by(
            models.ProcessingOutboxEvent.next_recovery_at.asc(),
            models.ProcessingOutboxEvent.created_at.asc(),
            models.ProcessingOutboxEvent.id.asc(),
        )
        .limit(batch_size)
        .all()
    )
    return [row[0] for row in rows]


def _requeue_failed_event(db: Session, event_id: str, now: datetime, max_cycles: int) -> bool:
    updated = (
        db.query(models.ProcessingOutboxEvent)
        .filter(models.ProcessingOutboxEvent.id == event_id)
        .filter(models.ProcessingOutboxEvent.status == "failed")
        .filter(models.ProcessingOutboxEvent.failure_disposition == PublicationFailureDisposition.TRANSIENT.value)
        .filter(models.ProcessingOutboxEvent.next_recovery_at.is_not(None))
        .filter(models.ProcessingOutboxEvent.next_recovery_at <= now)
        .filter(models.ProcessingOutboxEvent.recovery_cycle_count < max_cycles)
        .update(
            {
                "status": "pending",
                "attempt_count": 0,
                "next_attempt_at": None,
                "next_recovery_at": None,
                "recovery_cycle_count": models.ProcessingOutboxEvent.recovery_cycle_count + 1,
                "updated_at": now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return updated == 1


def reconcile_failed_processing_outbox_events(
    db: Session,
    *,
    enabled: bool | None = None,
    batch_size: int | None = None,
    max_cycles: int | None = None,
    now: datetime | None = None,
) -> ProcessingOutboxRecoveryResult:
    recovery_enabled = settings.PROCESSING_OUTBOX_RECOVERY_ENABLED if enabled is None else enabled
    if not recovery_enabled:
        return ProcessingOutboxRecoveryResult(disabled=True)

    recovery_now = now or _utc_now()
    selected_batch_size = settings.PROCESSING_OUTBOX_RECOVERY_BATCH_SIZE if batch_size is None else batch_size
    selected_max_cycles = settings.PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES if max_cycles is None else max_cycles
    event_ids = _eligible_event_ids(db, recovery_now, selected_batch_size, selected_max_cycles)

    requeued = sum(
        1
        for event_id in event_ids
        if _requeue_failed_event(db, event_id, recovery_now, selected_max_cycles)
    )
    return ProcessingOutboxRecoveryResult(
        eligible=len(event_ids),
        requeued=requeued,
        skipped=len(event_ids) - requeued,
    )
