from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import logging

from app.result_delivery.domain.failure_classification import classify_publication_failure
from app.result_delivery.ports.publisher import ProcessingResultPublisher
from app.result_delivery.ports.repository import ProcessingResultOutboxRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingOutboxRelayResult:
    claimed: int = 0
    published: int = 0
    retried: int = 0
    failed: int = 0
    skipped: int = 0
    disabled: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


@dataclass(frozen=True)
class ProcessingResultRelayPolicy:
    batch_size: int
    max_attempts: int
    retry_delay_seconds: int
    recovery_max_cycles: int
    recovery_cooldown_seconds: int


class RelayProcessingResultsApplicationService:
    def __init__(
        self,
        *,
        repository: ProcessingResultOutboxRepository,
        publisher: ProcessingResultPublisher,
        policy: ProcessingResultRelayPolicy,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._policy = policy
        self._clock = clock

    def relay_once(self, *, enabled: bool, batch_size: int | None = None) -> ProcessingOutboxRelayResult:
        if not enabled:
            logger.warning("processing outbox relay is disabled")
            return ProcessingOutboxRelayResult(disabled=True)
        selected_batch_size = self._policy.batch_size if batch_size is None else batch_size
        ids = self._repository.select_due_event_ids(now=self._clock(), limit=selected_batch_size)
        claimed = published = retried = failed = skipped = 0
        for event_id in ids:
            event = self._repository.claim(event_id, now=self._clock())
            if event is None:
                skipped += 1
                continue
            claimed += 1
            try:
                self._publisher.publish(event)
                if self._repository.finalize_published(event.id, now=self._clock()):
                    published += 1
                else:
                    skipped += 1
            except Exception as exc:
                classification = classify_publication_failure(exc)
                logger.warning(
                    "processing outbox publish failed disposition=%s category=%s attempt_count=%s",
                    classification.disposition.value,
                    classification.safe_category,
                    event.attempt_count,
                )
                retry = self._repository.record_publication_failure(
                    event.id,
                    classification=classification,
                    now=self._clock(),
                    max_attempts=self._policy.max_attempts,
                    retry_delay_seconds=self._policy.retry_delay_seconds,
                    recovery_max_cycles=self._policy.recovery_max_cycles,
                    recovery_cooldown_seconds=self._policy.recovery_cooldown_seconds,
                )
                if retry is None:
                    skipped += 1
                elif retry:
                    retried += 1
                else:
                    failed += 1
        return ProcessingOutboxRelayResult(claimed, published, retried, failed, skipped)
