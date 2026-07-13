from typing import Protocol

from app.result_delivery.domain.event import ProcessingResultEvent


class ProcessingResultPublisher(Protocol):
    def publish(self, event: ProcessingResultEvent) -> None:
        ...
