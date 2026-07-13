import logging
import time

from app.core.celery_app import celery_app
from app.processing.composition import (
    build_direct_upload_execution_service,
    build_processing_execution_service,
)
from app.processing.domain.models import (
    ProcessingExecutionCommand,
    ProcessingFailed,
    ProcessingSkipped,
    ProcessingSucceeded,
)
from app.processing.adapters.timing import log_processing_timing

logger = logging.getLogger(__name__)


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


@celery_app.task(name="process_asset_object", bind=True)
def process_asset_object_task(self, request: dict) -> dict:
    task_started_at = time.perf_counter()
    task_id = getattr(self.request, "id", None)
    command = ProcessingExecutionCommand.from_task_payload(request)
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
