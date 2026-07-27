from datetime import timedelta

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.processing.domain.models import (
    ProcessingClaimConflict,
    ProcessingFailed,
    ProcessingLease,
    ProcessingLeaseLost,
    ProcessingRequestCommand,
    ProcessingSucceeded,
)
from app.processing.ports.request_repository import ProcessingRequestState


def _request_state(request: models.ProcessingRequest) -> ProcessingRequestState:
    return ProcessingRequestState(
        event_id=request.event_id,
        asset_id=request.asset_id,
        status=request.status,
        task_id=request.celery_task_id,
        storage_bucket=request.storage_bucket,
        object_key=request.object_key,
    )


class SqlAlchemyProcessingRequestRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_or_create(self, command: ProcessingRequestCommand) -> ProcessingRequestState:
        existing = self._db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == command.event_id,
        ).first()
        if existing:
            return _request_state(existing)

        request = models.ProcessingRequest(
            event_id=command.event_id,
            asset_id=command.asset_id,
            workspace_id=command.workspace_id,
            owner_id=command.owner_id,
            storage_bucket=command.storage_bucket,
            object_key=command.object_key,
            original_filename=command.original_filename,
            content_type=command.content_type,
            size_bytes=command.size_bytes,
            status="accepted",
            occurred_at=command.occurred_at,
            requested_at=command.requested_at,
        )
        self._db.add(request)
        try:
            self._db.commit()
            self._db.refresh(request)
            return _request_state(request)
        except IntegrityError:
            self._db.rollback()
            existing = self._db.query(models.ProcessingRequest).filter(
                models.ProcessingRequest.event_id == command.event_id,
            ).one()
            return _request_state(existing)

    def mark_enqueued(self, event_id: str, task_id: str) -> ProcessingRequestState:
        updated = (
            self._db.query(models.ProcessingRequest)
            .filter(
                models.ProcessingRequest.event_id == event_id,
                models.ProcessingRequest.status == "accepted",
            )
            .update(
                {"celery_task_id": task_id, "status": "enqueued", "error": None},
                synchronize_session=False,
            )
        )
        if updated == 0:
            self._db.query(models.ProcessingRequest).filter(
                models.ProcessingRequest.event_id == event_id,
                models.ProcessingRequest.celery_task_id.is_(None),
            ).update({"celery_task_id": task_id}, synchronize_session=False)
        self._db.commit()
        request = self._db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == event_id,
        ).one()
        return _request_state(request)


class SqlAlchemyProcessingArtifactStore:
    def __init__(self, db: Session, *, lease_seconds: int) -> None:
        self.db = db
        self._lease_seconds = lease_seconds

    def claim(self, command, *, now) -> ProcessingLease | ProcessingClaimConflict:
        lease_expires_at = now + timedelta(seconds=self._lease_seconds)
        updated = (
            self.db.query(models.ProcessingRequest)
            .filter(
                models.ProcessingRequest.event_id == command.event_id,
                or_(
                    models.ProcessingRequest.status.in_(["accepted", "enqueued"]),
                    and_(
                        models.ProcessingRequest.status == "processing",
                        models.ProcessingRequest.lease_expires_at.is_not(None),
                        models.ProcessingRequest.lease_expires_at <= now,
                    ),
                ),
            )
            .update(
                {
                    "status": "processing",
                    "processing_started_at": now,
                    "lease_expires_at": lease_expires_at,
                    "attempt_count": models.ProcessingRequest.attempt_count + 1,
                    "error": None,
                    "updated_at": now,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        if updated:
            claimed = self.db.query(models.ProcessingRequest).filter(
                models.ProcessingRequest.event_id == command.event_id,
            ).one()
            return ProcessingLease(
                attempt_count=claimed.attempt_count,
                processing_started_at=claimed.processing_started_at,
                lease_expires_at=claimed.lease_expires_at,
            )
        existing = self.db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == command.event_id,
        ).first()
        return ProcessingClaimConflict(
            status=existing.status if existing else "missing",
            lease_expires_at=existing.lease_expires_at if existing else None,
        )

    def persist_success(self, outcome: ProcessingSucceeded, *, attempt_count: int) -> None:
        updated = (
            self.db.query(models.ProcessingRequest)
            .filter(
                models.ProcessingRequest.event_id == outcome.event_id,
                models.ProcessingRequest.status == "processing",
                models.ProcessingRequest.attempt_count == attempt_count,
            )
            .update(
                {
                    "status": "ready",
                    "segment_count": outcome.artifact.segment_count,
                    "error": None,
                    "processing_started_at": None,
                    "lease_expires_at": None,
                    "updated_at": outcome.completed_at,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            raise ProcessingLeaseLost(
                f"processing lease lost event_id={outcome.event_id} attempt_count={attempt_count}"
            )
        self.db.query(models.ProcessingRequestTranscript).filter(
            models.ProcessingRequestTranscript.processing_request_event_id == outcome.event_id,
        ).delete(synchronize_session=False)
        for row in outcome.artifact.rows:
            self.db.add(
                models.ProcessingRequestTranscript(
                    processing_request_event_id=outcome.event_id,
                    segment_index=row.segment_index,
                    text=row.text,
                    start_ms=row.start_ms,
                    end_ms=row.end_ms,
                )
            )

    def persist_failure(self, outcome: ProcessingFailed, *, attempt_count: int) -> None:
        updated = (
            self.db.query(models.ProcessingRequest)
            .filter(
                models.ProcessingRequest.event_id == outcome.event_id,
                models.ProcessingRequest.status == "processing",
                models.ProcessingRequest.attempt_count == attempt_count,
            )
            .update(
                {
                    "status": "failed",
                    "error": outcome.failure.diagnostic_message,
                    "processing_started_at": None,
                    "lease_expires_at": None,
                    "updated_at": outcome.completed_at,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            raise ProcessingLeaseLost(
                f"processing lease lost event_id={outcome.event_id} attempt_count={attempt_count}"
            )

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def close(self) -> None:
        self.db.close()


class SqlAlchemyDirectUploadArtifactStore:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._video = None

    def exists(self, video_id: int) -> bool:
        self._video = self._db.query(models.Video).filter(models.Video.id == video_id).first()
        return self._video is not None

    def persist_ready(self, video_id: int, segments: tuple[str, ...]) -> None:
        if segments:
            for index, segment in enumerate(segments):
                self._db.add(models.Transcript(video_id=video_id, segment_index=index, text=segment))
            self._db.commit()
        self._video.status = "ready"
        self._db.commit()

    def persist_failed(self, video_id: int) -> None:
        if self._video is None:
            self._video = self._db.query(models.Video).filter(models.Video.id == video_id).first()
        if self._video is not None:
            self._video.status = "failed"
            self._db.commit()

    def close(self) -> None:
        self._db.close()
