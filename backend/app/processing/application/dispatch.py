from dataclasses import dataclass
import logging

from app.processing.domain.models import ProcessingRequestCommand
from app.processing.ports.request_repository import ProcessingRequestRepository
from app.processing.ports.task_dispatcher import ProcessingTaskDispatcher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingAcceptance:
    event_id: str
    asset_id: str
    accepted: bool
    duplicate: bool
    task_id: str | None
    status: str


class DispatchProcessingApplicationService:
    def __init__(
        self,
        *,
        repository: ProcessingRequestRepository,
        dispatcher: ProcessingTaskDispatcher,
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher

    def dispatch(self, command: ProcessingRequestCommand) -> ProcessingAcceptance:
        request = self._repository.get_or_create(command)
        if request.status in {"enqueued", "processing", "ready", "failed"}:
            logger.info(
                "asset processing event already accepted event_id=%s asset_id=%s task_id=%s status=%s",
                request.event_id,
                request.asset_id,
                request.task_id,
                request.status,
            )
            return ProcessingAcceptance(
                event_id=request.event_id,
                asset_id=request.asset_id,
                accepted=True,
                duplicate=True,
                task_id=request.task_id,
                status=request.status,
            )

        dispatched = self._dispatcher.dispatch(command.to_execution_command())
        request = self._repository.mark_enqueued(command.event_id, dispatched.task_id)
        logger.info(
            "asset processing event accepted event_id=%s asset_id=%s bucket=%s object_key=%s task_id=%s",
            request.event_id,
            request.asset_id,
            request.storage_bucket,
            request.object_key,
            request.task_id,
        )
        return ProcessingAcceptance(
            event_id=request.event_id,
            asset_id=request.asset_id,
            accepted=True,
            duplicate=False,
            task_id=request.task_id,
            status=request.status,
        )
