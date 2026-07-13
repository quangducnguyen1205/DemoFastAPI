"""Stable publisher imports backed by the result-delivery feature."""

from app.config.settings import settings
from app.result_delivery.adapters.event_codec import ProcessingResultEventCodec
from app.result_delivery.adapters.kafka_publisher import (
    DisabledProcessingResultPublisher,
    KafkaProcessingResultPublisher,
    build_processing_result_publisher,
)
from app.result_delivery.adapters.sqlalchemy_repository import event_from_model
from app.result_delivery.domain.event import ProcessingResultEvent
from app.result_delivery.domain.failures import (
    PermanentProcessingResultPublisherError,
    ProcessingResultPublisherDisabledError,
    ProcessingResultPublisherError,
)
from app.result_delivery.ports.publisher import ProcessingResultPublisher

ProcessingOutboxPublisherError = ProcessingResultPublisherError
PermanentProcessingOutboxPublisherError = PermanentProcessingResultPublisherError
PublisherDisabledError = ProcessingResultPublisherDisabledError
ProcessingOutboxPublisher = ProcessingResultPublisher
DisabledProcessingOutboxPublisher = DisabledProcessingResultPublisher
KafkaProcessingOutboxPublisher = KafkaProcessingResultPublisher


def build_result_event_envelope(event) -> dict:
    neutral_event = event if isinstance(event, ProcessingResultEvent) else event_from_model(event)
    return ProcessingResultEventCodec().encode(neutral_event)


def build_processing_outbox_publisher():
    return build_processing_result_publisher()
