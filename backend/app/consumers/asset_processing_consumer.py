import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app import models as _models  # noqa: F401
from app.config.settings import settings
from app.core.database import SessionLocal
from app.events.asset_processing import EventValidationError, parse_asset_processing_requested_event
from app.events.asset_processing_v2 import parse_youtube_asset_processing_requested_event
from app.processing.domain.models import ConflictingProcessingRequestError
from app.processing.application.dispatch import ProcessingAcceptance
from app.bootstrap.consumer import build_processing_dispatch_service

logger = logging.getLogger(__name__)
_SAFE_LOG_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class MessageHandlingResult:
    accepted: bool
    duplicate: bool
    rejected: bool
    event_id: str | None = None
    reason: str | None = None
    celery_task_id: str | None = None


def _decode_event_context(raw_value: bytes | str | dict) -> dict[str, Any]:
    try:
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")
        event = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except Exception:
        return {"decode": "failed"}

    if not isinstance(event, dict):
        return {"decode": "non_object"}

    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}

    def safe_id(value):
        return (
            value
            if isinstance(value, str) and _SAFE_LOG_ID_PATTERN.fullmatch(value)
            else None
        )

    event_version = event.get("eventVersion")
    return {
        "eventId": safe_id(event.get("eventId")),
        "eventVersion": event_version if isinstance(event_version, int) else None,
        "aggregateId": safe_id(event.get("aggregateId")),
        "assetId": safe_id(payload.get("assetId")),
    }


def _parse_processing_event(
    raw_value: bytes | str | dict,
    *,
    topic: str | None,
):
    if topic == settings.KAFKA_ASSET_PROCESSING_TOPIC:
        return parse_asset_processing_requested_event(raw_value)
    if topic == settings.KAFKA_ASSET_PROCESSING_V2_TOPIC:
        return parse_youtube_asset_processing_requested_event(raw_value)
    if topic is not None:
        raise EventValidationError(f"unsupported processing topic '{topic}'")

    context = _decode_event_context(raw_value)
    if context.get("eventVersion") == 1:
        return parse_asset_processing_requested_event(raw_value)
    if context.get("eventVersion") == 2:
        return parse_youtube_asset_processing_requested_event(raw_value)
    raise EventValidationError(
        f"unsupported eventVersion '{context.get('eventVersion')}'"
    )


def handle_asset_processing_message(
    raw_value: bytes | str | dict,
    db: Session,
    topic: str | None = None,
) -> MessageHandlingResult:
    try:
        event = _parse_processing_event(raw_value, topic=topic)
    except EventValidationError:
        logger.warning(
            "rejecting asset processing event context=%s reason=validation_failed",
            _decode_event_context(raw_value),
        )
        return MessageHandlingResult(
            accepted=False,
            duplicate=False,
            rejected=True,
            reason="event validation failed",
        )

    try:
        acceptance: ProcessingAcceptance = build_processing_dispatch_service(db).dispatch(
            event.to_processing_command()
        )
    except ConflictingProcessingRequestError as exc:
        logger.warning(
            "rejecting conflicting processing event context=%s reason=%s",
            _decode_event_context(raw_value),
            exc,
        )
        return MessageHandlingResult(
            accepted=False,
            duplicate=False,
            rejected=True,
            event_id=getattr(event, "eventId", None),
            reason=str(exc),
        )
    return MessageHandlingResult(
        accepted=acceptance.accepted,
        duplicate=acceptance.duplicate,
        rejected=False,
        event_id=acceptance.event_id,
        celery_task_id=acceptance.task_id,
    )


class AssetProcessingKafkaConsumer:
    def __init__(self) -> None:
        self._stopped = False

    def stop(self, *_args) -> None:
        self._stopped = True

    def build_consumer(self):
        from kafka import KafkaConsumer

        return KafkaConsumer(
            settings.KAFKA_ASSET_PROCESSING_TOPIC,
            settings.KAFKA_ASSET_PROCESSING_V2_TOPIC,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS_LIST,
            group_id=settings.KAFKA_CONSUMER_GROUP,
            enable_auto_commit=False,
            auto_offset_reset=settings.KAFKA_AUTO_OFFSET_RESET,
            value_deserializer=lambda value: value,
        )

    def run_forever(self) -> None:
        logger.info(
            "starting asset processing Kafka consumer topics=%s,%s group=%s bootstrap=%s",
            settings.KAFKA_ASSET_PROCESSING_TOPIC,
            settings.KAFKA_ASSET_PROCESSING_V2_TOPIC,
            settings.KAFKA_CONSUMER_GROUP,
            settings.KAFKA_BOOTSTRAP_SERVERS,
        )
        while not self._stopped:
            consumer = None
            try:
                consumer = self.build_consumer()
                logger.info("asset processing Kafka consumer connected")
                for message in consumer:
                    if self._stopped:
                        break

                    db = SessionLocal()
                    try:
                        result = handle_asset_processing_message(
                            message.value,
                            db,
                            getattr(message, "topic", None),
                        )
                        if result.rejected:
                            logger.warning(
                                "committing rejected event offset to avoid blocking the partition reason=%s",
                                result.reason,
                            )
                        consumer.commit()
                    except Exception:
                        logger.exception("asset processing handoff or offset commit failed; offset left uncommitted")
                    finally:
                        db.close()
            except Exception:
                if self._stopped:
                    break
                logger.exception(
                    "asset processing Kafka consumer unavailable; retrying in %s seconds",
                    settings.KAFKA_RECONNECT_BACKOFF_SECONDS,
                )
                time.sleep(settings.KAFKA_RECONNECT_BACKOFF_SECONDS)
            finally:
                if consumer is not None:
                    consumer.close()


def main() -> None:
    from app.bootstrap.consumer import run_processing_consumer

    run_processing_consumer()


if __name__ == "__main__":
    main()
