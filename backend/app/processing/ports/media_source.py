from contextlib import AbstractContextManager
from typing import Protocol

from app.processing.domain.models import ProcessingExecutionCommandLike


class ProcessingMediaSource(Protocol):
    def acquire(self, command: ProcessingExecutionCommandLike) -> AbstractContextManager[str]:
        ...
