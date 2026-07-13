from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias


@dataclass(frozen=True)
class ProcessingRequestCommand:
    event_id: str
    event_type: str
    event_version: int
    aggregate_type: str
    aggregate_id: str
    occurred_at: str
    asset_id: str
    workspace_id: str | None
    owner_id: str | None
    storage_bucket: str
    object_key: str
    original_filename: str | None
    content_type: str
    size_bytes: int
    requested_at: str | None

    def to_execution_command(self) -> "ProcessingExecutionCommand":
        return ProcessingExecutionCommand(
            event_id=self.event_id,
            asset_id=self.asset_id,
            workspace_id=self.workspace_id,
            owner_id=self.owner_id,
            storage_bucket=self.storage_bucket,
            object_key=self.object_key,
            original_filename=self.original_filename,
            content_type=self.content_type,
            size_bytes=self.size_bytes,
        )


@dataclass(frozen=True)
class ProcessingExecutionCommand:
    event_id: str
    asset_id: str
    workspace_id: str | None
    owner_id: str | None
    storage_bucket: str
    object_key: str
    original_filename: str | None
    content_type: str
    size_bytes: int

@dataclass(frozen=True)
class ProcessingTranscriptRow:
    segment_index: int
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None


@dataclass(frozen=True)
class ProcessingArtifact:
    rows: tuple[ProcessingTranscriptRow, ...]

    @property
    def segment_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class ProcessingFailure:
    code: str
    diagnostic_message: str
    cause: Exception


@dataclass(frozen=True)
class ProcessingSucceeded:
    event_id: str
    asset_id: str
    artifact: ProcessingArtifact
    completed_at: datetime


@dataclass(frozen=True)
class ProcessingFailed:
    event_id: str
    asset_id: str
    failure: ProcessingFailure
    completed_at: datetime


ProcessingOutcome: TypeAlias = ProcessingSucceeded | ProcessingFailed


@dataclass(frozen=True)
class ProcessingSkipped:
    event_id: str
    asset_id: str
    status: str


ProcessingExecutionResult: TypeAlias = ProcessingOutcome | ProcessingSkipped
