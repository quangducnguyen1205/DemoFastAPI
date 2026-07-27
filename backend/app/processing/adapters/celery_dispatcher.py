from collections.abc import Callable
import re

from app.processing.domain.models import (
    ProcessingExecutionCommand,
    ProcessingExecutionCommandLike,
    YouTubeProcessingExecutionCommand,
)
from app.processing.ports.task_dispatcher import ProcessingDispatch

_YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


def encode_youtube_processing_task_payload(
    command: YouTubeProcessingExecutionCommand,
) -> dict:
    return {
        "eventId": command.event_id,
        "assetId": command.asset_id,
        "workspaceId": command.workspace_id,
        "ownerId": command.owner_id,
        "youtubeVideoId": command.youtube_video_id,
    }


def decode_youtube_processing_task_payload(
    payload: dict,
) -> YouTubeProcessingExecutionCommand:
    youtube_video_id = payload["youtubeVideoId"]
    if (
        not isinstance(youtube_video_id, str)
        or not _YOUTUBE_VIDEO_ID_PATTERN.fullmatch(youtube_video_id)
    ):
        raise ValueError("invalid YouTube processing task video ID")
    return YouTubeProcessingExecutionCommand(
        event_id=payload["eventId"],
        asset_id=payload["assetId"],
        workspace_id=payload.get("workspaceId"),
        owner_id=payload.get("ownerId"),
        youtube_video_id=youtube_video_id,
    )


class CeleryYouTubeProcessingTaskDispatcher:
    def __init__(self, enqueue: Callable[..., object] | None = None) -> None:
        self._enqueue = enqueue

    def dispatch(self, command: YouTubeProcessingExecutionCommand) -> ProcessingDispatch:
        if self._enqueue is None:
            from app.tasks.video_tasks import process_youtube_asset_task

            enqueue = process_youtube_asset_task.apply_async
        else:
            enqueue = self._enqueue
        task_id = f"youtube-processing-{command.event_id}"
        result = enqueue(
            args=[encode_youtube_processing_task_payload(command)],
            task_id=task_id,
        )
        return ProcessingDispatch(task_id=getattr(result, "id", task_id))


class SourceAwareCeleryProcessingTaskDispatcher:
    def __init__(
        self,
        *,
        object_storage_dispatcher: CeleryProcessingTaskDispatcher | None = None,
        youtube_dispatcher: CeleryYouTubeProcessingTaskDispatcher | None = None,
    ) -> None:
        self._object_storage_dispatcher = (
            object_storage_dispatcher or CeleryProcessingTaskDispatcher()
        )
        self._youtube_dispatcher = (
            youtube_dispatcher or CeleryYouTubeProcessingTaskDispatcher()
        )

    def dispatch(self, command: ProcessingExecutionCommandLike) -> ProcessingDispatch:
        if isinstance(command, YouTubeProcessingExecutionCommand):
            return self._youtube_dispatcher.dispatch(command)
        return self._object_storage_dispatcher.dispatch(command)
