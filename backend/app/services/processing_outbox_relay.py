import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models
from app.config.settings import settings
from app.services.processing_outbox_failure import (
    PublicationFailureClassification,
    PublicationFailureDisposition,
    classify_publication_failure,
)
from app.services.processing_outbox_publisher import (
    ProcessingOutboxPublisher,
    build_processing_outbox_publisher,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingOutboxRelayResult:
    claimed: int = 0
    published: int = 0
    retried: int = 0
    failed: int = 0
    skipped: int = 0
    disabled: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _due_pending_query(db: Session, now: datetime):
    return (
        db.query(models.ProcessingOutboxEvent)
        .filter(models.ProcessingOutboxEvent.status == "pending")
        .filter(
            or_(
                models.ProcessingOutboxEvent.next_attempt_at.is_(None),
                models.ProcessingOutboxEvent.next_attempt_at <= now,
            )
        )
        .order_by(models.ProcessingOutboxEvent.created_at.asc(), models.ProcessingOutboxEvent.id.asc())
    )


def _claim_event(db: Session, event_id: str, now: datetime) -> models.ProcessingOutboxEvent | None:
    updated = (
        db.query(models.ProcessingOutboxEvent)
        .filter(models.ProcessingOutboxEvent.id == event_id)
        .filter(models.ProcessingOutboxEvent.status == "pending")
        .filter(
            or_(
                models.ProcessingOutboxEvent.next_attempt_at.is_(None),
                models.ProcessingOutboxEvent.next_attempt_at <= now,
            )
        )
        .update(
            {
                "status": "publishing",
                "last_error": None,
                "updated_at": now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated == 0:
        return None
    event = db.query(models.ProcessingOutboxEvent).filter(
        models.ProcessingOutboxEvent.id == event_id,
    ).one()
    db.expunge(event)
    db.rollback()
    return event


def _mark_published(db: Session, event: models.ProcessingOutboxEvent, now: datetime) -> bool:
    publishing_event = (
        db.query(models.ProcessingOutboxEvent)
        .filter(models.ProcessingOutboxEvent.id == event.id)
        .filter(models.ProcessingOutboxEvent.status == "publishing")
        .one_or_none()
    )
    if publishing_event is None:
        db.rollback()
        return False

    publishing_event.status = "published"
    publishing_event.published_at = now
    publishing_event.next_attempt_at = None
    publishing_event.last_error = None
    publishing_event.failure_disposition = None
    publishing_event.next_recovery_at = None
    publishing_event.last_failure_category = None
    publishing_event.recovery_exhausted_at = None
    publishing_event.updated_at = now
    db.commit()
    return True


def _mark_publish_failed(
    db: Session,
    event: models.ProcessingOutboxEvent,
    classification: PublicationFailureClassification,
    now: datetime,
) -> bool | None:
    publishing_event = (
        db.query(models.ProcessingOutboxEvent)
        .filter(models.ProcessingOutboxEvent.id == event.id)
        .filter(models.ProcessingOutboxEvent.status == "publishing")
        .one_or_none()
    )
    if publishing_event is None:
        db.rollback()
        return None

    attempts = (publishing_event.attempt_count or 0) + 1
    publishing_event.attempt_count = attempts
    publishing_event.last_error = classification.safe_category
    publishing_event.last_failure_category = classification.safe_category
    publishing_event.updated_at = now
    publishing_event.recovery_exhausted_at = None
    if attempts >= settings.PROCESSING_OUTBOX_RELAY_MAX_ATTEMPTS:
        publishing_event.status = "failed"
        publishing_event.next_attempt_at = None
        recovery_cycles = publishing_event.recovery_cycle_count or 0
        if classification.disposition == PublicationFailureDisposition.TRANSIENT:
            if recovery_cycles >= settings.PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES:
                publishing_event.failure_disposition = PublicationFailureDisposition.RECOVERY_EXHAUSTED.value
                publishing_event.next_recovery_at = None
                publishing_event.recovery_exhausted_at = now
            else:
                publishing_event.failure_disposition = PublicationFailureDisposition.TRANSIENT.value
                publishing_event.next_recovery_at = now + timedelta(
                    seconds=settings.PROCESSING_OUTBOX_RECOVERY_COOLDOWN_SECONDS
                )
        else:
            publishing_event.failure_disposition = classification.disposition.value
            publishing_event.next_recovery_at = None
        db.commit()
        return False

    publishing_event.status = "pending"
    publishing_event.next_attempt_at = now + timedelta(seconds=settings.PROCESSING_OUTBOX_RELAY_RETRY_DELAY_SECONDS)
    publishing_event.failure_disposition = None
    publishing_event.next_recovery_at = None
    db.commit()
    return True


def run_processing_outbox_relay_once(
    db: Session,
    *,
    publisher: ProcessingOutboxPublisher | None = None,
    enabled: bool | None = None,
    batch_size: int | None = None,
) -> ProcessingOutboxRelayResult:
    relay_enabled = settings.PROCESSING_OUTBOX_RELAY_ENABLED if enabled is None else enabled
    if not relay_enabled:
        if enabled is None:
            logger.warning("processing outbox relay is disabled; set PROCESSING_OUTBOX_RELAY_ENABLED=true")
        else:
            logger.warning("processing outbox relay is disabled")
        return ProcessingOutboxRelayResult(disabled=True)

    publisher = publisher or build_processing_outbox_publisher()
    now = _utc_now()
    selected_batch_size = settings.PROCESSING_OUTBOX_RELAY_BATCH_SIZE if batch_size is None else batch_size
    due_events = _due_pending_query(db, now).limit(selected_batch_size).all()

    claimed = published = retried = failed = skipped = 0
    for due_event in due_events:
        event = _claim_event(db, due_event.id, _utc_now())
        if event is None:
            skipped += 1
            continue

        claimed += 1
        try:
            publisher.publish(event)
            if _mark_published(db, event, _utc_now()):
                published += 1
            else:
                skipped += 1
        except Exception as exc:
            classification = classify_publication_failure(exc)
            logger.warning(
                "processing outbox publish failed disposition=%s category=%s attempt_count=%s",
                classification.disposition.value,
                classification.safe_category,
                event.attempt_count,
            )
            will_retry = _mark_publish_failed(db, event, classification, _utc_now())
            if will_retry is None:
                skipped += 1
            elif will_retry:
                retried += 1
            else:
                failed += 1

    return ProcessingOutboxRelayResult(
        claimed=claimed,
        published=published,
        retried=retried,
        failed=failed,
        skipped=skipped,
    )
