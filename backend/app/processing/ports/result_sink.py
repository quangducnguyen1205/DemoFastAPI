from typing import Protocol

from app.processing.domain.models import ProcessingOutcome


class ProcessingResultSink(Protocol):
    def record(self, outcome: ProcessingOutcome) -> None:
        ...
