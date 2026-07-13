from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from app.result_delivery.ports.repository import ProcessingResultOutboxRepository


@dataclass(frozen=True)
class ProcessingOutboxRecoveryResult:
    eligible: int = 0
    requeued: int = 0
    skipped: int = 0
    disabled: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


class ReconcileFailedProcessingResultsApplicationService:
    def __init__(
        self,
        *,
        repository: ProcessingResultOutboxRepository,
        batch_size: int,
        max_cycles: int,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._batch_size = batch_size
        self._max_cycles = max_cycles
        self._clock = clock

    def reconcile_once(
        self,
        *,
        enabled: bool,
        batch_size: int | None = None,
        max_cycles: int | None = None,
        now: datetime | None = None,
    ) -> ProcessingOutboxRecoveryResult:
        if not enabled:
            return ProcessingOutboxRecoveryResult(disabled=True)
        selected_now = now or self._clock()
        selected_batch_size = self._batch_size if batch_size is None else batch_size
        selected_max_cycles = self._max_cycles if max_cycles is None else max_cycles
        ids = self._repository.select_recovery_event_ids(
            now=selected_now,
            limit=selected_batch_size,
            max_cycles=selected_max_cycles,
        )
        requeued = sum(
            1
            for event_id in ids
            if self._repository.requeue_failed(event_id, now=selected_now, max_cycles=selected_max_cycles)
        )
        return ProcessingOutboxRecoveryResult(
            eligible=len(ids),
            requeued=requeued,
            skipped=len(ids) - requeued,
        )
