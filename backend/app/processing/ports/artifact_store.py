from datetime import datetime
from typing import Protocol

from app.processing.domain.models import (
    ProcessingClaimConflict,
    ProcessingExecutionCommandLike,
    ProcessingFailed,
    ProcessingLease,
    ProcessingSucceeded,
)


class ProcessingArtifactStore(Protocol):
    def claim(
        self,
        command: ProcessingExecutionCommandLike,
        *,
        now: datetime,
    ) -> ProcessingLease | ProcessingClaimConflict:
        """Atomically acquire a lease or describe why the request cannot be claimed."""
        ...

    def persist_success(self, outcome: ProcessingSucceeded, *, attempt_count: int) -> None:
        ...

    def persist_failure(self, outcome: ProcessingFailed, *, attempt_count: int) -> None:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...

    def close(self) -> None:
        ...


class DirectUploadArtifactStore(Protocol):
    def exists(self, video_id: int) -> bool:
        ...

    def persist_ready(self, video_id: int, segments: tuple[str, ...]) -> None:
        ...

    def persist_failed(self, video_id: int) -> None:
        ...

    def close(self) -> None:
        ...
