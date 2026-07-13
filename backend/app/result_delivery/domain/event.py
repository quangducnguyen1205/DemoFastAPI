from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ProcessingResultEvent:
    id: str
    event_type: str
    event_version: int
    aggregate_type: str
    aggregate_id: str
    event_key: str
    causation_event_id: str
    occurred_at: datetime
    payload: dict[str, Any]
    attempt_count: int = 0
