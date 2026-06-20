import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models
from app.config.settings import settings
from app.services.processing_outbox import safe_error_message
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
    return db.query(models.ProcessingOutboxEvent).filter(
        models.ProcessingOutboxEvent.id == event_id,
    ).one()


def _mark_published(db: Session, event: models.ProcessingOutboxEvent, now: datetime) -> None:
    event.status = "published"
    event.published_at = now
    event.next_attempt_at = None
    event.last_error = None
    event.updated_at = now
    db.commit()


def _mark_publish_failed(
    db: Session,
    event: models.ProcessingOutboxEvent,
    exc: Exception,
    now: datetime,
) -> bool:
    attempts = (event.attempt_count or 0) + 1
    event.attempt_count = attempts
    event.last_error = safe_error_message(exc)
    event.updated_at = now
    if attempts >= settings.PROCESSING_OUTBOX_RELAY_MAX_ATTEMPTS:
        event.status = "failed"
        event.next_attempt_at = None
        db.commit()
        return False

    event.status = "pending"
    event.next_attempt_at = now + timedelta(seconds=settings.PROCESSING_OUTBOX_RELAY_RETRY_DELAY_SECONDS)
    db.commit()
    return True


def run_processing_outbox_relay_once(
    db: Session,
    *,
    publisher: ProcessingOutboxPublisher | None = None,
) -> ProcessingOutboxRelayResult:
    if not settings.PROCESSING_OUTBOX_RELAY_ENABLED:
        logger.warning("processing outbox relay is disabled; set PROCESSING_OUTBOX_RELAY_ENABLED=true")
        return ProcessingOutboxRelayResult(disabled=True)

    publisher = publisher or build_processing_outbox_publisher()
    now = _utc_now()
    due_events = _due_pending_query(db, now).limit(settings.PROCESSING_OUTBOX_RELAY_BATCH_SIZE).all()

    claimed = published = retried = failed = skipped = 0
    for due_event in due_events:
        event = _claim_event(db, due_event.id, _utc_now())
        if event is None:
            skipped += 1
            continue

        claimed += 1
        try:
            publisher.publish(event)
            _mark_published(db, event, _utc_now())
            published += 1
        except Exception as exc:
            logger.exception(
                "processing outbox publish failed event_id=%s event_type=%s attempt_count=%s",
                event.id,
                event.event_type,
                event.attempt_count,
            )
            will_retry = _mark_publish_failed(db, event, exc, _utc_now())
            if will_retry:
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
