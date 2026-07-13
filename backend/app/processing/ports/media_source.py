from contextlib import AbstractContextManager
from typing import Protocol

from app.processing.domain.models import ProcessingExecutionCommand


class ProcessingMediaSource(Protocol):
    def acquire(self, command: ProcessingExecutionCommand) -> AbstractContextManager[str]:
        ...
