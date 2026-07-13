import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.processing.domain.models import (
    ProcessingArtifact,
    ProcessingFailed,
    ProcessingFailure,
    ProcessingSucceeded,
    ProcessingTranscriptRow,
)
from app.result_delivery.adapters.event_codec import ProcessingResultEventCodec
from app.result_delivery.adapters.kafka_publisher import KafkaProcessingResultPublisher
from app.result_delivery.adapters.sqlalchemy_repository import SqlAlchemyProcessingResultOutboxRepository
from app.result_delivery.application.record_result import RecordProcessingResultApplicationService
from app.result_delivery.application.relay import (
    ProcessingResultRelayPolicy,
    RelayProcessingResultsApplicationService,
)
from app.result_delivery.domain.event import ProcessingResultEvent
from app.result_delivery.domain.failures import (
    PermanentProcessingResultPublisherError,
    TransientProcessingResultPublisherError,
)


NOW = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)


def success_outcome() -> ProcessingSucceeded:
    return ProcessingSucceeded(
        "request-1",
        "asset-1",
        ProcessingArtifact(
            (
                ProcessingTranscriptRow(0, "first"),
                ProcessingTranscriptRow(1, "second"),
            )
        ),
        NOW,
    )


def failed_outcome() -> ProcessingFailed:
    error = RuntimeError("token=private-value provider failed")
    return ProcessingFailed(
        "request-1",
        "asset-1",
        ProcessingFailure("PROCESSING_FAILED", str(error), error),
        NOW,
    )


def ready_event(event_id: str = "result-1") -> ProcessingResultEvent:
    return ProcessingResultEvent(
        id=event_id,
        event_type="transcript.ready",
        event_version=1,
        aggregate_type="ASSET",
        aggregate_id="asset-1",
        event_key="asset-1",
        causation_event_id="request-1",
        occurred_at=NOW,
        payload={
            "assetId": "asset-1",
            "processingRequestId": "request-1",
            "status": "ready",
            "segmentCount": 2,
            "completedAt": "2026-07-13T00:00:00Z",
        },
    )


class RecordProcessingResultApplicationServiceTest(unittest.TestCase):
    def test_success_maps_to_one_deterministic_durable_intent(self) -> None:
        repository = MagicMock()
        repository.append.side_effect = lambda event: event
        service = RecordProcessingResultApplicationService(
            repository,
            event_id_factory=lambda: "result-1",
        )
        event = service.record(success_outcome())
        self.assertEqual(event, ready_event())
        repository.append.assert_called_once_with(event)

    def test_failure_uses_the_frozen_contract_and_redacts_sensitive_values(self) -> None:
        repository = MagicMock()
        repository.append.side_effect = lambda event: event
        service = RecordProcessingResultApplicationService(
            repository,
            event_id_factory=lambda: "result-failed",
        )
        event = service.record(failed_outcome())
        self.assertEqual(event.event_type, "asset.processing.failed")
        self.assertEqual(event.event_version, 1)
        self.assertEqual(event.event_key, "asset-1")
        self.assertEqual(
            set(event.payload),
            {"assetId", "processingRequestId", "status", "errorCode", "errorMessage", "completedAt"},
        )
        self.assertEqual(event.payload["errorCode"], "PROCESSING_FAILED")
        self.assertEqual(event.payload["errorMessage"], "token=[redacted] provider failed")


class ProcessingResultEventCodecTest(unittest.TestCase):
    def test_success_envelope_is_the_frozen_golden_contract(self) -> None:
        self.assertEqual(
            ProcessingResultEventCodec().encode(ready_event()),
            {
                "eventId": "result-1",
                "eventType": "transcript.ready",
                "eventVersion": 1,
                "aggregateType": "ASSET",
                "aggregateId": "asset-1",
                "eventKey": "asset-1",
                "causationEventId": "request-1",
                "occurredAt": "2026-07-13T00:00:00Z",
                "payload": {
                    "assetId": "asset-1",
                    "processingRequestId": "request-1",
                    "status": "ready",
                    "segmentCount": 2,
                    "completedAt": "2026-07-13T00:00:00Z",
                },
            },
        )

    def test_unknown_type_and_extra_payload_fields_fail_permanently(self) -> None:
        unknown = ready_event()
        object.__setattr__(unknown, "event_type", "unknown")
        with self.assertRaises(PermanentProcessingResultPublisherError):
            ProcessingResultEventCodec().encode(unknown)
        extra = ready_event("result-2")
        extra.payload["transcript"] = "must-not-leak"
        with self.assertRaises(PermanentProcessingResultPublisherError):
            ProcessingResultEventCodec().encode(extra)


class SqlAlchemyProcessingResultOutboxRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_append_is_idempotent_by_causation_and_event_type(self) -> None:
        db = self.Session()
        repository = SqlAlchemyProcessingResultOutboxRepository(db)
        first = repository.append(ready_event())
        db.commit()
        second = repository.append(ready_event("different-id"))
        self.assertEqual(first.id, "result-1")
        self.assertEqual(second.id, "result-1")
        from app import models

        self.assertEqual(db.query(models.ProcessingOutboxEvent).count(), 1)
        db.close()

    def test_claim_is_compare_and_set_and_only_one_session_wins(self) -> None:
        db1 = self.Session()
        db2 = self.Session()
        repository1 = SqlAlchemyProcessingResultOutboxRepository(db1)
        repository2 = SqlAlchemyProcessingResultOutboxRepository(db2)
        repository1.append(ready_event())
        db1.commit()
        self.assertIsNotNone(repository1.claim("result-1", now=NOW))
        self.assertIsNone(repository2.claim("result-1", now=NOW))
        db1.close()
        db2.close()

    def test_publish_failure_returns_to_pending_then_exhausts_with_existing_limits(self) -> None:
        db = self.Session()
        repository = SqlAlchemyProcessingResultOutboxRepository(db)
        repository.append(ready_event())
        db.commit()
        repository.claim("result-1", now=NOW)
        from app.result_delivery.domain.failure_classification import classify_publication_failure

        retry = repository.record_publication_failure(
            "result-1",
            classification=classify_publication_failure(TransientProcessingResultPublisherError()),
            now=NOW,
            max_attempts=2,
            retry_delay_seconds=60,
            recovery_max_cycles=3,
            recovery_cooldown_seconds=60,
        )
        self.assertTrue(retry)
        claimed = repository.claim("result-1", now=NOW + timedelta(seconds=60))
        self.assertIsNotNone(claimed)
        retry = repository.record_publication_failure(
            "result-1",
            classification=classify_publication_failure(TransientProcessingResultPublisherError()),
            now=NOW + timedelta(seconds=60),
            max_attempts=2,
            retry_delay_seconds=60,
            recovery_max_cycles=3,
            recovery_cooldown_seconds=60,
        )
        self.assertFalse(retry)
        from app import models

        saved = db.query(models.ProcessingOutboxEvent).filter_by(id="result-1").one()
        self.assertEqual(saved.status, "failed")
        self.assertEqual(saved.failure_disposition, "transient")
        self.assertEqual(saved.next_recovery_at, (NOW + timedelta(seconds=120)).replace(tzinfo=None))
        db.close()


class RelayProcessingResultsApplicationServiceTest(unittest.TestCase):
    def test_relay_publishes_neutral_event_and_finalizes_through_repository(self) -> None:
        repository = MagicMock()
        repository.select_due_event_ids.return_value = ("result-1",)
        repository.claim.return_value = ready_event()
        repository.finalize_published.return_value = True
        publisher = MagicMock()
        service = RelayProcessingResultsApplicationService(
            repository=repository,
            publisher=publisher,
            policy=ProcessingResultRelayPolicy(10, 5, 60, 3, 60),
            clock=lambda: NOW,
        )
        result = service.relay_once(enabled=True)
        publisher.publish.assert_called_once_with(ready_event())
        repository.finalize_published.assert_called_once_with("result-1", now=NOW)
        self.assertEqual(result.published, 1)

    def test_transient_publisher_failure_uses_the_same_failure_transition(self) -> None:
        repository = MagicMock()
        repository.select_due_event_ids.return_value = ("result-1",)
        repository.claim.return_value = ready_event()
        repository.record_publication_failure.return_value = True
        publisher = MagicMock()
        publisher.publish.side_effect = TransientProcessingResultPublisherError("down")
        service = RelayProcessingResultsApplicationService(
            repository=repository,
            publisher=publisher,
            policy=ProcessingResultRelayPolicy(10, 5, 60, 3, 60),
            clock=lambda: NOW,
        )
        result = service.relay_once(enabled=True)
        self.assertEqual(result.retried, 1)
        classification = repository.record_publication_failure.call_args.kwargs["classification"]
        self.assertEqual(classification.disposition.value, "transient")


class KafkaProcessingResultPublisherTest(unittest.TestCase):
    def test_adapter_preserves_topic_key_ack_timeout_and_idempotence(self) -> None:
        publisher = KafkaProcessingResultPublisher(
            topic="asset.processing.result.v1",
            bootstrap_servers=["broker:9092"],
            send_timeout_seconds=10,
        )
        future = MagicMock()
        future.get.return_value = SimpleNamespace(
            topic="asset.processing.result.v1", partition=0, offset=1
        )
        producer = MagicMock()
        producer.send.return_value = future
        publisher._producer = producer
        publisher.publish(ready_event())
        producer.send.assert_called_once_with(
            "asset.processing.result.v1",
            key="asset-1",
            value=ProcessingResultEventCodec().encode(ready_event()),
        )
        future.get.assert_called_once_with(timeout=10)
        config = publisher._producer_config()
        self.assertEqual(config["acks"], "all")
        self.assertTrue(config["enable_idempotence"])


if __name__ == "__main__":
    unittest.main()
