from dataclasses import dataclass
from typing import Protocol

from app.processing.domain.models import ProcessingRequestCommandLike


@dataclass(frozen=True)
class ProcessingRequestState:
    event_id: str
    asset_id: str
    status: str
    task_id: str | None
    storage_bucket: str | None
    object_key: str | None
    source_type: str = "OBJECT_STORAGE"
    youtube_video_id: str | None = None


class ProcessingRequestRepository(Protocol):
    def get_or_create(self, command: ProcessingRequestCommandLike) -> ProcessingRequestState:
        ...

    def mark_enqueued(self, event_id: str, task_id: str) -> ProcessingRequestState:
        ...
