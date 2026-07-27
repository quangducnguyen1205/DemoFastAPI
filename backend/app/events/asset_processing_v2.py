import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.events.asset_processing import EventValidationError
from app.processing.domain.models import YouTubeProcessingRequestCommand


EXPECTED_EVENT_TYPE = "asset.processing.requested"
EXPECTED_EVENT_VERSION = 2
YOUTUBE_VIDEO_ID_MAX_LENGTH = 64
_YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class YouTubeAssetProcessingRequestedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assetId: str = Field(min_length=1, max_length=64)
    workspaceId: str | None = Field(default=None, max_length=64)
    ownerId: str | None = Field(default=None, max_length=255)
    sourceType: Literal["YOUTUBE"]
    youtubeVideoId: str = Field(min_length=1, max_length=YOUTUBE_VIDEO_ID_MAX_LENGTH)
    requestedAt: str | None = None

    @field_validator("assetId", "youtubeVideoId")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("workspaceId", "ownerId")
    @classmethod
    def _strip_optional_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("youtubeVideoId")
    @classmethod
    def _validate_youtube_video_id(cls, value: str) -> str:
        if not _YOUTUBE_VIDEO_ID_PATTERN.fullmatch(value):
            raise ValueError("must contain only A-Z, a-z, 0-9, underscore, or hyphen")
        return value


class YouTubeAssetProcessingRequestedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=1, max_length=64)
    eventType: str = Field(min_length=1)
    eventVersion: int
    aggregateType: str = Field(min_length=1)
    aggregateId: str = Field(min_length=1, max_length=64)
    occurredAt: str = Field(min_length=1)
    payload: YouTubeAssetProcessingRequestedPayload

    @field_validator("eventId", "eventType", "aggregateType", "aggregateId", "occurredAt")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def _validate_event_identity(self) -> "YouTubeAssetProcessingRequestedEvent":
        if self.eventType != EXPECTED_EVENT_TYPE:
            raise ValueError(f"unsupported eventType '{self.eventType}'")
        if self.eventVersion != EXPECTED_EVENT_VERSION:
            raise ValueError(f"unsupported eventVersion '{self.eventVersion}'")
        return self

    def to_processing_command(self) -> YouTubeProcessingRequestCommand:
        return YouTubeProcessingRequestCommand(
            event_id=self.eventId,
            event_type=self.eventType,
            event_version=self.eventVersion,
            aggregate_type=self.aggregateType,
            aggregate_id=self.aggregateId,
            occurred_at=self.occurredAt,
            asset_id=self.payload.assetId,
            workspace_id=self.payload.workspaceId,
            owner_id=self.payload.ownerId,
            youtube_video_id=self.payload.youtubeVideoId,
            requested_at=self.payload.requestedAt,
        )


def parse_youtube_asset_processing_requested_event(
    raw_event: bytes | str | dict[str, Any],
) -> YouTubeAssetProcessingRequestedEvent:
    try:
        if isinstance(raw_event, bytes):
            event_dict = json.loads(raw_event.decode("utf-8"))
        elif isinstance(raw_event, str):
            event_dict = json.loads(raw_event)
        else:
            event_dict = raw_event
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EventValidationError(f"event is not valid JSON: {exc}") from exc

    try:
        return YouTubeAssetProcessingRequestedEvent.model_validate(event_dict)
    except ValidationError as exc:
        raise EventValidationError(str(exc)) from exc
