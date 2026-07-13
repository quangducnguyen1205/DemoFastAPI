from collections.abc import Callable

from app.processing.domain.models import ProcessingExecutionCommand
from app.processing.ports.task_dispatcher import ProcessingDispatch


class CeleryProcessingTaskDispatcher:
    def __init__(self, enqueue: Callable[..., object] | None = None) -> None:
        self._enqueue = enqueue

    def dispatch(self, command: ProcessingExecutionCommand) -> ProcessingDispatch:
        if self._enqueue is None:
            from app.tasks.video_tasks import process_asset_object_task

            enqueue = process_asset_object_task.apply_async
        else:
            enqueue = self._enqueue
        task_id = f"asset-processing-{command.event_id}"
        result = enqueue(args=[command.to_task_payload()], task_id=task_id)
        return ProcessingDispatch(task_id=getattr(result, "id", task_id))
