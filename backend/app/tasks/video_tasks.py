from datetime import UTC, datetime
import logging
import math
import time

from app.config.settings import settings
from app.core.celery_app import celery_app
from app.processing.adapters.celery_dispatcher import decode_processing_task_payload
from app.bootstrap.worker import (
    build_direct_upload_execution_service,
    build_processing_execution_service,
)
from app.processing.domain.models import (
    ProcessingFailed,
    ProcessingSkipped,
    ProcessingSucceeded,
)
from app.processing.adapters.timing import log_processing_timing

logger = logging.getLogger(__name__)


def _lease_retry_delay_seconds(
    retry_at: datetime,
    *,
    now: datetime | None = None,
    poll_seconds: int | None = None,
    visibility_timeout_seconds: int | None = None,
) -> int:
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    poll_limit = poll_seconds or settings.PROCESSING_LEASE_RETRY_POLL_SECONDS
    visibility_limit = (
        visibility_timeout_seconds
        or settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS
    )
    broker_safe_limit = max(1, visibility_limit - 1)
    remaining_seconds = max(1, math.ceil((retry_at - current).total_seconds()))
    return min(remaining_seconds, poll_limit, broker_safe_limit)


@celery_app.task(name="process_video", bind=True)
def process_video_task(self, video_id: int, abs_video_path: str) -> dict:
    task_started_at = time.perf_counter()
    task_id = getattr(self.request, "id", None)
    service = build_direct_upload_execution_service()
    try:
        result = service.execute(video_id=video_id, media_path=abs_video_path, task_id=task_id)
        log_processing_timing(
            "total_task_ms",
            (time.perf_counter() - task_started_at) * 1000,
            task_id=task_id,
            video_id=video_id,
            status=result["status"],
            segment_count=len(result.get("segments", ())) if result["status"] == "ready" else None,
        )
        return result
    finally:
        service.close()


@celery_app.task(
    name="process_asset_object",
    bind=True,
    acks_late=True,
    acks_on_failure_or_timeout=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
def process_asset_object_task(self, request: dict) -> dict:
    task_started_at = time.perf_counter()
    task_id = getattr(self.request, "id", None)
    command = decode_processing_task_payload(request)
    logger.info(
        "starting asset object processing event_id=%s asset_id=%s bucket=%s object_key=%s content_type=%s task_id=%s",
        command.event_id,
        command.asset_id,
        command.storage_bucket,
        command.object_key,
        command.content_type,
        task_id,
    )
    service = build_processing_execution_service()
    try:
        outcome = service.execute(command, task_id=task_id)
        if isinstance(outcome, ProcessingSkipped):
            if outcome.status == "processing" and outcome.retry_at is not None:
                retry_delay = _lease_retry_delay_seconds(outcome.retry_at)
                logger.info(
                    "polling active processing lease event_id=%s asset_id=%s retry_in_seconds=%s lease_expires_at=%s",
                    outcome.event_id,
                    outcome.asset_id,
                    retry_delay,
                    outcome.retry_at.isoformat(),
                )
                raise self.retry(countdown=retry_delay)
            result = {"status": outcome.status, "asset_id": outcome.asset_id, "duplicate": True}
        elif isinstance(outcome, ProcessingSucceeded):
            result = {
                "status": "ready",
                "asset_id": outcome.asset_id,
                "segments": [row.text for row in outcome.artifact.rows],
            }
        elif isinstance(outcome, ProcessingFailed):
            result = {
                "status": "failed",
                "asset_id": outcome.asset_id,
                "error": outcome.failure.diagnostic_message,
            }
        else:  # pragma: no cover - the use case has an exhaustive result union
            raise TypeError(f"unsupported processing outcome: {type(outcome).__name__}")
        log_processing_timing(
            "total_task_ms",
            (time.perf_counter() - task_started_at) * 1000,
            task_id=task_id,
            asset_id=command.asset_id,
            status=result["status"],
            segment_count=len(result.get("segments", ())) if result["status"] == "ready" else None,
        )
        return result
    finally:
        service.close()
