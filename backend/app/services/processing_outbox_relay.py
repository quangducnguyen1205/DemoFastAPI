"""Stable relay entrypoints backed by one result-delivery application service."""

from datetime import UTC, datetime

from app.config.settings import settings
from app.result_delivery.adapters.kafka_publisher import build_processing_result_publisher
from app.result_delivery.adapters.sqlalchemy_repository import SqlAlchemyProcessingResultOutboxRepository
from app.result_delivery.application.relay import (
    ProcessingOutboxRelayResult,
    ProcessingResultRelayPolicy,
    RelayProcessingResultsApplicationService,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _policy() -> ProcessingResultRelayPolicy:
    return ProcessingResultRelayPolicy(
        batch_size=settings.PROCESSING_OUTBOX_RELAY_BATCH_SIZE,
        max_attempts=settings.PROCESSING_OUTBOX_RELAY_MAX_ATTEMPTS,
        retry_delay_seconds=settings.PROCESSING_OUTBOX_RELAY_RETRY_DELAY_SECONDS,
        recovery_max_cycles=settings.PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES,
        recovery_cooldown_seconds=settings.PROCESSING_OUTBOX_RECOVERY_COOLDOWN_SECONDS,
    )


def _claim_event(db, event_id: str, now: datetime):
    return SqlAlchemyProcessingResultOutboxRepository(db).claim(event_id, now=now)


def _mark_published(db, event, now: datetime) -> bool:
    return SqlAlchemyProcessingResultOutboxRepository(db).finalize_published(event.id, now=now)


def _mark_publish_failed(db, event, classification, now: datetime) -> bool | None:
    policy = _policy()
    return SqlAlchemyProcessingResultOutboxRepository(db).record_publication_failure(
        event.id,
        classification=classification,
        now=now,
        max_attempts=policy.max_attempts,
        retry_delay_seconds=policy.retry_delay_seconds,
        recovery_max_cycles=policy.recovery_max_cycles,
        recovery_cooldown_seconds=policy.recovery_cooldown_seconds,
    )


def run_processing_outbox_relay_once(
    db,
    *,
    publisher=None,
    enabled: bool | None = None,
    batch_size: int | None = None,
) -> ProcessingOutboxRelayResult:
    relay_enabled = settings.PROCESSING_OUTBOX_RELAY_ENABLED if enabled is None else enabled
    service = RelayProcessingResultsApplicationService(
        repository=SqlAlchemyProcessingResultOutboxRepository(db),
        publisher=publisher or build_processing_result_publisher(),
        policy=_policy(),
    )
    return service.relay_once(enabled=relay_enabled, batch_size=batch_size)
