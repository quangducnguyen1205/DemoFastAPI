from dataclasses import dataclass
import logging
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.events.asset_processing import AssetProcessingRequestedEvent
from app import models
from app.tasks.video_tasks import process_asset_object_task

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingAcceptance:
    event_id: str
    asset_id: str
    accepted: bool
    duplicate: bool
    celery_task_id: str | None
    status: str


def _get_or_create_processing_request(db: Session, event: AssetProcessingRequestedEvent) -> models.ProcessingRequest:
    existing = db.query(models.ProcessingRequest).filter(
        models.ProcessingRequest.event_id == event.eventId,
    ).first()
    if existing:
        return existing

    request = models.ProcessingRequest(
        event_id=event.eventId,
        asset_id=event.payload.assetId,
        workspace_id=event.payload.workspaceId,
        owner_id=event.payload.ownerId,
        storage_bucket=event.payload.storageBucket,
        object_key=event.payload.objectKey,
        original_filename=event.payload.originalFilename,
        content_type=event.payload.contentType,
        size_bytes=event.payload.sizeBytes,
        status="accepted",
        occurred_at=event.occurredAt,
        requested_at=event.payload.requestedAt,
    )
    db.add(request)
    try:
        db.commit()
        db.refresh(request)
        return request
    except IntegrityError:
        db.rollback()
        return db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == event.eventId,
        ).one()


def accept_processing_event(
    db: Session,
    event: AssetProcessingRequestedEvent,
    *,
    enqueue: Callable[..., object] | None = None,
) -> ProcessingAcceptance:
    request = _get_or_create_processing_request(db, event)

    if request.status in {"enqueued", "processing", "ready", "failed"}:
        logger.info(
            "asset processing event already accepted event_id=%s asset_id=%s task_id=%s status=%s",
            request.event_id,
            request.asset_id,
            request.celery_task_id,
            request.status,
        )
        return ProcessingAcceptance(
            event_id=request.event_id,
            asset_id=request.asset_id,
            accepted=True,
            duplicate=True,
            celery_task_id=request.celery_task_id,
            status=request.status,
        )

    task_payload = event.to_celery_payload()
    task_id = f"asset-processing-{event.eventId}"
    enqueue_callable = enqueue or process_asset_object_task.apply_async
    async_result = enqueue_callable(args=[task_payload], task_id=task_id)
    celery_task_id = getattr(async_result, "id", task_id)

    updated = (
        db.query(models.ProcessingRequest)
        .filter(
            models.ProcessingRequest.event_id == event.eventId,
            models.ProcessingRequest.status == "accepted",
        )
        .update(
            {
                "celery_task_id": celery_task_id,
                "status": "enqueued",
                "error": None,
            },
            synchronize_session=False,
        )
    )
    if updated == 0:
        db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == event.eventId,
            models.ProcessingRequest.celery_task_id.is_(None),
        ).update({"celery_task_id": celery_task_id}, synchronize_session=False)
    db.commit()
    request = db.query(models.ProcessingRequest).filter(
        models.ProcessingRequest.event_id == event.eventId,
    ).one()

    logger.info(
        "asset processing event accepted event_id=%s asset_id=%s bucket=%s object_key=%s task_id=%s",
        request.event_id,
        request.asset_id,
        request.storage_bucket,
        request.object_key,
        request.celery_task_id,
    )
    return ProcessingAcceptance(
        event_id=request.event_id,
        asset_id=request.asset_id,
        accepted=True,
        duplicate=False,
        celery_task_id=request.celery_task_id,
        status=request.status,
    )
