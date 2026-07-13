from dataclasses import dataclass
from typing import Protocol

from app.processing.domain.models import ProcessingExecutionCommand


@dataclass(frozen=True)
class ProcessingDispatch:
    task_id: str


class ProcessingTaskDispatcher(Protocol):
    def dispatch(self, command: ProcessingExecutionCommand) -> ProcessingDispatch:
        ...
