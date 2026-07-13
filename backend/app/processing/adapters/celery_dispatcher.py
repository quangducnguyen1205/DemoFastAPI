from collections.abc import Callable

from app.processing.domain.models import ProcessingExecutionCommand
from app.processing.ports.task_dispatcher import ProcessingDispatch


def encode_processing_task_payload(command: ProcessingExecutionCommand) -> dict:
    return {
        "eventId": command.event_id,
        "assetId": command.asset_id,
        "workspaceId": command.workspace_id,
        "ownerId": command.owner_id,
        "bucket": command.storage_bucket,
        "objectKey": command.object_key,
        "contentType": command.content_type,
        "originalFilename": command.original_filename,
        "sizeBytes": command.size_bytes,
    }


def decode_processing_task_payload(payload: dict) -> ProcessingExecutionCommand:
    return ProcessingExecutionCommand(
        event_id=payload["eventId"],
        asset_id=payload["assetId"],
        workspace_id=payload.get("workspaceId"),
        owner_id=payload.get("ownerId"),
        storage_bucket=payload["bucket"],
        object_key=payload["objectKey"],
        original_filename=payload.get("originalFilename"),
        content_type=payload["contentType"],
        size_bytes=payload["sizeBytes"],
    )


class CeleryProcessingTaskDispatcher:
    def __init__(self, enqueue: Callable[..., object] | None = None) -> None:
        self._enqueue = enqueue

    def dispatch(self, command: ProcessingExecutionCommand) -> ProcessingDispatch:
        if self._enqueue is None:
            from app.tasks.video_tasks import process_asset_object_task

            enqueue = process_asset_object_task.apply_async
        else:
            enqueue = self._enqueue
        task_id = f"asset-processing-{command.event_id}"
        result = enqueue(args=[encode_processing_task_payload(command)], task_id=task_id)
        return ProcessingDispatch(task_id=getattr(result, "id", task_id))
