from typing import Protocol

from app.processing.domain.models import ProcessingExecutionCommand, ProcessingFailed, ProcessingSucceeded


class ProcessingArtifactStore(Protocol):
    def claim(self, command: ProcessingExecutionCommand) -> str | None:
        """Claim work and return the existing status when it cannot be claimed."""
        ...

    def persist_success(self, outcome: ProcessingSucceeded) -> None:
        ...

    def persist_failure(self, outcome: ProcessingFailed) -> None:
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
