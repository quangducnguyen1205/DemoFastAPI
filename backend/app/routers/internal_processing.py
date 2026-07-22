import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.core.database import get_db
from app.schemas.transcripts import ProcessingTranscriptRowRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/processing-requests", tags=["internal-processing"])


def _normalize_processing_request_id(processing_request_id: str) -> str:
    try:
        return str(UUID(processing_request_id))
    except (TypeError, ValueError) as exc:
        logger.info("invalid processing request id for transcript artifact retrieval")
        raise HTTPException(status_code=400, detail="Invalid processing request id") from exc


def _ensure_usable_rows(
    request: models.ProcessingRequest,
    rows: list[models.ProcessingRequestTranscript],
) -> None:
    if not rows:
        logger.warning(
            "ready processing request has no transcript artifacts event_id=%s asset_id=%s",
            request.event_id,
            request.asset_id,
        )
        raise HTTPException(status_code=409, detail="Processing transcript artifacts are not available")

    if any(
        row.segment_index is None
        or row.segment_index < 0
        or not row.text
        or not row.text.strip()
        or row.created_at is None
        or (row.start_ms is None) != (row.end_ms is None)
        or (row.start_ms is not None and (row.start_ms < 0 or row.end_ms < row.start_ms))
        for row in rows
    ):
        logger.warning(
            "ready processing request has unusable transcript artifacts event_id=%s asset_id=%s row_count=%s",
            request.event_id,
            request.asset_id,
            len(rows),
        )
        raise HTTPException(status_code=409, detail="Processing transcript artifacts are not usable")


@router.get("/{processingRequestId}/transcript-rows", response_model=list[ProcessingTranscriptRowRead])
def get_processing_request_transcript_rows(
    processingRequestId: str,
    db: Session = Depends(get_db),
) -> list[ProcessingTranscriptRowRead]:
    normalized_request_id = _normalize_processing_request_id(processingRequestId)
    request = (
        db.query(models.ProcessingRequest)
        .filter(models.ProcessingRequest.event_id == normalized_request_id)
        .first()
    )

    if request is None:
        logger.info(
            "processing request not found for transcript artifact retrieval event_id=%s",
            normalized_request_id,
        )
        raise HTTPException(status_code=404, detail="Processing request not found")

    if request.status == "failed":
        logger.info(
            "failed processing request cannot return transcript artifacts event_id=%s asset_id=%s",
            request.event_id,
            request.asset_id,
        )
        raise HTTPException(status_code=409, detail="Processing request failed")

    if request.status != "ready":
        logger.info(
            "processing request transcript artifacts not ready event_id=%s asset_id=%s status=%s",
            request.event_id,
            request.asset_id,
            request.status,
        )
        raise HTTPException(status_code=409, detail="Processing request is not ready")

    rows = (
        db.query(models.ProcessingRequestTranscript)
        .filter(models.ProcessingRequestTranscript.processing_request_event_id == request.event_id)
        .order_by(
            models.ProcessingRequestTranscript.segment_index.asc(),
            models.ProcessingRequestTranscript.id.asc(),
        )
        .all()
    )
    _ensure_usable_rows(request, rows)

    logger.info(
        "processing transcript artifacts retrieved event_id=%s asset_id=%s row_count=%s",
        request.event_id,
        request.asset_id,
        len(rows),
    )
    return [
        ProcessingTranscriptRowRead(
            id=str(row.id),
            video_id=row.processing_request_event_id,
            segment_index=row.segment_index,
            start_ms=row.start_ms,
            end_ms=row.end_ms,
            text=row.text,
            created_at=row.created_at,
        )
        for row in rows
    ]
