import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.processing.domain.models import ProcessingRequestCommand


EXPECTED_EVENT_TYPE = "asset.processing.requested"
EXPECTED_EVENT_VERSION = 1


class EventValidationError(ValueError):
    pass


class AssetProcessingRequestedPayload(BaseModel):
    assetId: str = Field(min_length=1)
    workspaceId: str | None = None
    ownerId: str | None = None
    storageBucket: str = Field(min_length=1)
    objectKey: str = Field(min_length=1)
    originalFilename: str | None = None
    contentType: str = Field(min_length=1)
    sizeBytes: int = Field(ge=0)
    requestedAt: str | None = None

    @field_validator("assetId", "storageBucket", "objectKey", "contentType")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    def to_celery_payload(self, event_id: str) -> dict[str, Any]:
        return {
            "eventId": event_id,
            "assetId": self.assetId,
            "workspaceId": self.workspaceId,
            "ownerId": self.ownerId,
            "bucket": self.storageBucket,
            "objectKey": self.objectKey,
            "contentType": self.contentType,
            "originalFilename": self.originalFilename,
            "sizeBytes": self.sizeBytes,
        }


class AssetProcessingRequestedEvent(BaseModel):
    eventId: str = Field(min_length=1)
    eventType: str = Field(min_length=1)
    eventVersion: int
    aggregateType: str = Field(min_length=1)
    aggregateId: str = Field(min_length=1)
    occurredAt: str = Field(min_length=1)
    payload: AssetProcessingRequestedPayload

    @field_validator("eventId", "eventType", "aggregateType", "aggregateId", "occurredAt")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def _validate_event_identity(self) -> "AssetProcessingRequestedEvent":
        if self.eventType != EXPECTED_EVENT_TYPE:
            raise ValueError(f"unsupported eventType '{self.eventType}'")
        if self.eventVersion != EXPECTED_EVENT_VERSION:
            raise ValueError(f"unsupported eventVersion '{self.eventVersion}'")
        return self

    def to_celery_payload(self) -> dict[str, Any]:
        return self.payload.to_celery_payload(self.eventId)

    def to_processing_command(self) -> ProcessingRequestCommand:
        return ProcessingRequestCommand(
            event_id=self.eventId,
            event_type=self.eventType,
            event_version=self.eventVersion,
            aggregate_type=self.aggregateType,
            aggregate_id=self.aggregateId,
            occurred_at=self.occurredAt,
            asset_id=self.payload.assetId,
            workspace_id=self.payload.workspaceId,
            owner_id=self.payload.ownerId,
            storage_bucket=self.payload.storageBucket,
            object_key=self.payload.objectKey,
            original_filename=self.payload.originalFilename,
            content_type=self.payload.contentType,
            size_bytes=self.payload.sizeBytes,
            requested_at=self.payload.requestedAt,
        )


def parse_asset_processing_requested_event(raw_event: bytes | str | dict[str, Any]) -> AssetProcessingRequestedEvent:
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
        return AssetProcessingRequestedEvent.model_validate(event_dict)
    except ValidationError as exc:
        raise EventValidationError(str(exc)) from exc
