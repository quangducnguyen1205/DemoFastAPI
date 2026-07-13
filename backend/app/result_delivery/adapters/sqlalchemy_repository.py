from datetime import timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models
from app.result_delivery.domain.event import ProcessingResultEvent
from app.result_delivery.domain.failure_classification import (
    PublicationFailureClassification,
    PublicationFailureDisposition,
)


def event_from_model(event: models.ProcessingOutboxEvent) -> ProcessingResultEvent:
    return ProcessingResultEvent(
        id=event.id,
        event_type=event.event_type,
        event_version=event.event_version,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        event_key=event.event_key,
        causation_event_id=event.causation_event_id,
        occurred_at=event.occurred_at,
        payload=event.payload,
        attempt_count=event.attempt_count or 0,
    )


class SqlAlchemyProcessingResultOutboxRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def append(self, event: ProcessingResultEvent) -> ProcessingResultEvent:
        existing = self.db.query(models.ProcessingOutboxEvent).filter(
            models.ProcessingOutboxEvent.causation_event_id == event.causation_event_id,
            models.ProcessingOutboxEvent.event_type == event.event_type,
        ).first()
        if existing:
            return event_from_model(existing)
        self.db.add(
            models.ProcessingOutboxEvent(
                id=event.id,
                event_type=event.event_type,
                event_version=event.event_version,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_key=event.event_key,
                causation_event_id=event.causation_event_id,
                occurred_at=event.occurred_at,
                payload=event.payload,
                status="pending",
                attempt_count=0,
            )
        )
        return event

    def select_due_event_ids(self, *, now, limit: int) -> tuple[str, ...]:
        rows = (
            self.db.query(models.ProcessingOutboxEvent.id)
            .filter(models.ProcessingOutboxEvent.status == "pending")
            .filter(
                or_(
                    models.ProcessingOutboxEvent.next_attempt_at.is_(None),
                    models.ProcessingOutboxEvent.next_attempt_at <= now,
                )
            )
            .order_by(models.ProcessingOutboxEvent.created_at.asc(), models.ProcessingOutboxEvent.id.asc())
            .limit(limit)
            .all()
        )
        return tuple(row[0] for row in rows)

    def claim(self, event_id: str, *, now) -> ProcessingResultEvent | None:
        updated = (
            self.db.query(models.ProcessingOutboxEvent)
            .filter(models.ProcessingOutboxEvent.id == event_id)
            .filter(models.ProcessingOutboxEvent.status == "pending")
            .filter(
                or_(
                    models.ProcessingOutboxEvent.next_attempt_at.is_(None),
                    models.ProcessingOutboxEvent.next_attempt_at <= now,
                )
            )
            .update(
                {"status": "publishing", "last_error": None, "updated_at": now},
                synchronize_session=False,
            )
        )
        self.db.commit()
        if updated == 0:
            return None
        row = self.db.query(models.ProcessingOutboxEvent).filter(
            models.ProcessingOutboxEvent.id == event_id,
        ).one()
        event = event_from_model(row)
        self.db.expunge(row)
        self.db.rollback()
        return event

    def finalize_published(self, event_id: str, *, now) -> bool:
        row = (
            self.db.query(models.ProcessingOutboxEvent)
            .filter(models.ProcessingOutboxEvent.id == event_id)
            .filter(models.ProcessingOutboxEvent.status == "publishing")
            .one_or_none()
        )
        if row is None:
            self.db.rollback()
            return False
        row.status = "published"
        row.published_at = now
        row.next_attempt_at = None
        row.last_error = None
        row.failure_disposition = None
        row.next_recovery_at = None
        row.last_failure_category = None
        row.recovery_exhausted_at = None
        row.updated_at = now
        self.db.commit()
        return True

    def record_publication_failure(
        self,
        event_id: str,
        *,
        classification: PublicationFailureClassification,
        now,
        max_attempts: int,
        retry_delay_seconds: int,
        recovery_max_cycles: int,
        recovery_cooldown_seconds: int,
    ) -> bool | None:
        row = (
            self.db.query(models.ProcessingOutboxEvent)
            .filter(models.ProcessingOutboxEvent.id == event_id)
            .filter(models.ProcessingOutboxEvent.status == "publishing")
            .one_or_none()
        )
        if row is None:
            self.db.rollback()
            return None
        attempts = (row.attempt_count or 0) + 1
        row.attempt_count = attempts
        row.last_error = classification.safe_category
        row.last_failure_category = classification.safe_category
        row.updated_at = now
        row.recovery_exhausted_at = None
        if attempts >= max_attempts:
            row.status = "failed"
            row.next_attempt_at = None
            recovery_cycles = row.recovery_cycle_count or 0
            if classification.disposition == PublicationFailureDisposition.TRANSIENT:
                if recovery_cycles >= recovery_max_cycles:
                    row.failure_disposition = PublicationFailureDisposition.RECOVERY_EXHAUSTED.value
                    row.next_recovery_at = None
                    row.recovery_exhausted_at = now
                else:
                    row.failure_disposition = PublicationFailureDisposition.TRANSIENT.value
                    row.next_recovery_at = now + timedelta(seconds=recovery_cooldown_seconds)
            else:
                row.failure_disposition = classification.disposition.value
                row.next_recovery_at = None
            self.db.commit()
            return False
        row.status = "pending"
        row.next_attempt_at = now + timedelta(seconds=retry_delay_seconds)
        row.failure_disposition = None
        row.next_recovery_at = None
        self.db.commit()
        return True

    def select_recovery_event_ids(self, *, now, limit: int, max_cycles: int) -> tuple[str, ...]:
        rows = (
            self.db.query(models.ProcessingOutboxEvent.id)
            .filter(models.ProcessingOutboxEvent.status == "failed")
            .filter(
                models.ProcessingOutboxEvent.failure_disposition
                == PublicationFailureDisposition.TRANSIENT.value
            )
            .filter(models.ProcessingOutboxEvent.next_recovery_at.is_not(None))
            .filter(models.ProcessingOutboxEvent.next_recovery_at <= now)
            .filter(models.ProcessingOutboxEvent.recovery_cycle_count < max_cycles)
            .order_by(
                models.ProcessingOutboxEvent.next_recovery_at.asc(),
                models.ProcessingOutboxEvent.created_at.asc(),
                models.ProcessingOutboxEvent.id.asc(),
            )
            .limit(limit)
            .all()
        )
        return tuple(row[0] for row in rows)

    def requeue_failed(self, event_id: str, *, now, max_cycles: int) -> bool:
        updated = (
            self.db.query(models.ProcessingOutboxEvent)
            .filter(models.ProcessingOutboxEvent.id == event_id)
            .filter(models.ProcessingOutboxEvent.status == "failed")
            .filter(
                models.ProcessingOutboxEvent.failure_disposition
                == PublicationFailureDisposition.TRANSIENT.value
            )
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
        self.db.commit()
        return updated == 1
