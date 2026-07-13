from app.config.settings import settings
from app.result_delivery.adapters.kafka_publisher import build_processing_result_publisher
from app.result_delivery.adapters.sqlalchemy_repository import SqlAlchemyProcessingResultOutboxRepository
from app.result_delivery.application.reconcile import ReconcileFailedProcessingResultsApplicationService
from app.result_delivery.application.relay import (
    ProcessingResultRelayPolicy,
    RelayProcessingResultsApplicationService,
)


def build_result_publisher():
    return build_processing_result_publisher()


def result_relay_policy() -> ProcessingResultRelayPolicy:
    return ProcessingResultRelayPolicy(
        batch_size=settings.PROCESSING_OUTBOX_RELAY_BATCH_SIZE,
        max_attempts=settings.PROCESSING_OUTBOX_RELAY_MAX_ATTEMPTS,
        retry_delay_seconds=settings.PROCESSING_OUTBOX_RELAY_RETRY_DELAY_SECONDS,
        recovery_max_cycles=settings.PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES,
        recovery_cooldown_seconds=settings.PROCESSING_OUTBOX_RECOVERY_COOLDOWN_SECONDS,
    )


def build_result_relay_service(db, publisher) -> RelayProcessingResultsApplicationService:
    return RelayProcessingResultsApplicationService(
        repository=SqlAlchemyProcessingResultOutboxRepository(db),
        publisher=publisher,
        policy=result_relay_policy(),
    )


def build_result_reconciliation_service(db) -> ReconcileFailedProcessingResultsApplicationService:
    return ReconcileFailedProcessingResultsApplicationService(
        repository=SqlAlchemyProcessingResultOutboxRepository(db),
        batch_size=settings.PROCESSING_OUTBOX_RECOVERY_BATCH_SIZE,
        max_cycles=settings.PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES,
    )
