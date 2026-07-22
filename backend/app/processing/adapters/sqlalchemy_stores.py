from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.processing.domain.models import (
    ProcessingFailed,
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
    def __init__(self, db: Session) -> None:
        self.db = db

    def claim(self, command) -> str | None:
        updated = (
            self.db.query(models.ProcessingRequest)
            .filter(
                models.ProcessingRequest.event_id == command.event_id,
                models.ProcessingRequest.status.in_(["accepted", "enqueued"]),
            )
            .update({"status": "processing", "error": None}, synchronize_session=False)
        )
        self.db.commit()
        if updated:
            return None
        existing = self.db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == command.event_id,
        ).first()
        return existing.status if existing else "missing"

    def persist_success(self, outcome: ProcessingSucceeded) -> None:
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
        request = self.db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == outcome.event_id,
        ).one()
        request.status = "ready"
        request.segment_count = outcome.artifact.segment_count
        request.error = None

    def persist_failure(self, outcome: ProcessingFailed) -> None:
        request = self.db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == outcome.event_id,
        ).first()
        if request:
            request.status = "failed"
            request.error = outcome.failure.diagnostic_message

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
