"""Stable reconciliation entrypoints backed by result delivery."""

from datetime import UTC, datetime

from app.config.settings import settings
from app.result_delivery.adapters.sqlalchemy_repository import SqlAlchemyProcessingResultOutboxRepository
from app.result_delivery.application.reconcile import (
    ProcessingOutboxRecoveryResult,
    ReconcileFailedProcessingResultsApplicationService,
)
from app.result_delivery.domain.outbox_state import ProcessingOutboxState, is_recovery_eligible as _eligible


def _utc_now() -> datetime:
    return datetime.now(UTC)


def is_recovery_eligible(event, *, now: datetime, max_cycles: int) -> bool:
    return _eligible(
        ProcessingOutboxState(
            status=event.status,
            failure_disposition=event.failure_disposition,
            next_recovery_at=event.next_recovery_at,
            recovery_cycle_count=event.recovery_cycle_count or 0,
        ),
        now=now,
        max_cycles=max_cycles,
    )


def _requeue_failed_event(db, event_id: str, now: datetime, max_cycles: int) -> bool:
    return SqlAlchemyProcessingResultOutboxRepository(db).requeue_failed(
        event_id,
        now=now,
        max_cycles=max_cycles,
    )


def reconcile_failed_processing_outbox_events(
    db,
    *,
    enabled: bool | None = None,
    batch_size: int | None = None,
    max_cycles: int | None = None,
    now: datetime | None = None,
) -> ProcessingOutboxRecoveryResult:
    recovery_enabled = settings.PROCESSING_OUTBOX_RECOVERY_ENABLED if enabled is None else enabled
    service = ReconcileFailedProcessingResultsApplicationService(
        repository=SqlAlchemyProcessingResultOutboxRepository(db),
        batch_size=settings.PROCESSING_OUTBOX_RECOVERY_BATCH_SIZE,
        max_cycles=settings.PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES,
    )
    return service.reconcile_once(
        enabled=recovery_enabled,
        batch_size=batch_size,
        max_cycles=max_cycles,
        now=now,
    )
