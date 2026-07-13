from dataclasses import dataclass
from typing import Protocol

from app.processing.domain.models import ProcessingRequestCommand


@dataclass(frozen=True)
class ProcessingRequestState:
    event_id: str
    asset_id: str
    status: str
    task_id: str | None
    storage_bucket: str
    object_key: str


class ProcessingRequestRepository(Protocol):
    def get_or_create(self, command: ProcessingRequestCommand) -> ProcessingRequestState:
        ...

    def mark_enqueued(self, event_id: str, task_id: str) -> ProcessingRequestState:
        ...
