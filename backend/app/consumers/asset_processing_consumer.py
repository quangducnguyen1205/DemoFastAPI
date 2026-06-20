import json
import logging
import signal
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app import models as _models  # noqa: F401
from app.config.settings import settings
from app.core.database import Base, SessionLocal, engine
from app.events.asset_processing import EventValidationError, parse_asset_processing_requested_event
from app.services.processing_requests import ProcessingAcceptance, accept_processing_event

logger = logging.getLogger(__name__)


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
    return {
        "eventId": event.get("eventId"),
        "eventType": event.get("eventType"),
        "eventVersion": event.get("eventVersion"),
        "aggregateId": event.get("aggregateId"),
        "assetId": payload.get("assetId"),
        "objectKey": payload.get("objectKey"),
    }


def handle_asset_processing_message(raw_value: bytes | str | dict, db: Session) -> MessageHandlingResult:
    try:
        event = parse_asset_processing_requested_event(raw_value)
    except EventValidationError as exc:
        logger.warning(
            "rejecting asset processing event context=%s reason=%s",
            _decode_event_context(raw_value),
            exc,
        )
        return MessageHandlingResult(accepted=False, duplicate=False, rejected=True, reason=str(exc))

    acceptance: ProcessingAcceptance = accept_processing_event(db, event)
    return MessageHandlingResult(
        accepted=acceptance.accepted,
        duplicate=acceptance.duplicate,
        rejected=False,
        event_id=acceptance.event_id,
        celery_task_id=acceptance.celery_task_id,
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
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS_LIST,
            group_id=settings.KAFKA_CONSUMER_GROUP,
            enable_auto_commit=False,
            auto_offset_reset=settings.KAFKA_AUTO_OFFSET_RESET,
            value_deserializer=lambda value: value,
        )

    def run_forever(self) -> None:
        logger.info(
            "starting asset processing Kafka consumer topic=%s group=%s bootstrap=%s",
            settings.KAFKA_ASSET_PROCESSING_TOPIC,
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
                        result = handle_asset_processing_message(message.value, db)
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
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    Base.metadata.create_all(bind=engine)
    runner = AssetProcessingKafkaConsumer()
    signal.signal(signal.SIGTERM, runner.stop)
    signal.signal(signal.SIGINT, runner.stop)
    runner.run_forever()


if __name__ == "__main__":
    main()
