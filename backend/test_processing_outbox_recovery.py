import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import models
from app.core.database import Base
from app.core.schema import ensure_processing_outbox_recovery_schema
from app.relays import processing_outbox_auto_relay
from app.result_delivery.domain.failure_classification import (
    PublicationFailureClassification,
    PublicationFailureDisposition,
    classify_publication_failure,
)
from app.result_delivery.domain.failures import (
    PermanentProcessingResultPublisherError,
    TransientProcessingResultPublisherError,
)
from app.result_delivery.domain.outbox_state import ProcessingOutboxState, is_recovery_eligible
from app.result_delivery.adapters.sqlalchemy_repository import SqlAlchemyProcessingResultOutboxRepository
from app.result_delivery.application.reconcile import ReconcileFailedProcessingResultsApplicationService
from app.result_delivery.application.relay import (
    ProcessingResultRelayPolicy,
    RelayProcessingResultsApplicationService,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_event(event_id: str = "event-1") -> models.ProcessingOutboxEvent:
    return models.ProcessingOutboxEvent(
        id=event_id,
        event_type="transcript.ready",
        event_version=1,
        aggregate_type="ASSET",
        aggregate_id="asset-1",
        event_key="asset-1",
        causation_event_id="request-1",
        occurred_at=now_utc(),
        payload={"assetId": "asset-1", "processingRequestId": "request-1", "status": "ready"},
        status="pending",
        attempt_count=0,
        recovery_cycle_count=0,
    )


class PublicationFailureClassifierTest(unittest.TestCase):
    def test_typed_broker_timeout_connection_and_wrapped_failures_are_transient(self) -> None:
        broker_failure = TransientProcessingResultPublisherError("unavailable")
        self.assertEqual(
            classify_publication_failure(broker_failure).disposition,
            PublicationFailureDisposition.TRANSIENT,
        )
        try:
            raise RuntimeError("wrapper") from broker_failure
        except RuntimeError as wrapped:
            self.assertEqual(
                classify_publication_failure(wrapped).disposition,
                PublicationFailureDisposition.TRANSIENT,
            )

        self.assertEqual(
            classify_publication_failure(TimeoutError()).disposition,
            PublicationFailureDisposition.TRANSIENT,
        )
        self.assertEqual(
            classify_publication_failure(ConnectionError()).disposition,
            PublicationFailureDisposition.TRANSIENT,
        )

    def test_invalid_event_is_permanent_and_unknown_fails_closed(self) -> None:
        self.assertEqual(
            classify_publication_failure(PermanentProcessingResultPublisherError("invalid")).disposition,
            PublicationFailureDisposition.PERMANENT,
        )
        self.assertEqual(
            classify_publication_failure(RuntimeError("unexpected")).disposition,
            PublicationFailureDisposition.UNKNOWN,
        )
        self.assertEqual(
            classify_publication_failure(None).disposition,
            PublicationFailureDisposition.UNKNOWN,
        )


class ProcessingOutboxRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_historical_unknown_permanent_and_pre_cooldown_rows_are_not_eligible(self) -> None:
        now = now_utc()
        historical = new_event("historical")
        historical.status = "failed"
        historical.failure_disposition = PublicationFailureDisposition.UNKNOWN.value
        historical.attempt_count = 5
        transient_future = new_event("future")
        transient_future.status = "failed"
        transient_future.failure_disposition = PublicationFailureDisposition.TRANSIENT.value
        transient_future.next_recovery_at = now + timedelta(seconds=60)
        transient_future.attempt_count = 5
        permanent = new_event("permanent")
        permanent.status = "failed"
        permanent.failure_disposition = PublicationFailureDisposition.PERMANENT.value
        permanent.attempt_count = 5

        for event in (historical, transient_future, permanent):
            state = ProcessingOutboxState(
                event.status,
                event.failure_disposition,
                event.next_recovery_at,
                event.recovery_cycle_count or 0,
            )
            self.assertFalse(is_recovery_eligible(state, now=now, max_cycles=3))

    def test_transient_row_requeues_once_after_cooldown_and_preserves_identity(self) -> None:
        db = self.Session()
        now = now_utc()
        event = new_event()
        original_payload = dict(event.payload)
        event.status = "failed"
        event.attempt_count = 5
        event.failure_disposition = PublicationFailureDisposition.TRANSIENT.value
        event.last_failure_category = "kafka_retryable_failure"
        event.next_recovery_at = now - timedelta(seconds=1)
        db.add(event)
        db.commit()

        first = ReconcileFailedProcessingResultsApplicationService(
            repository=SqlAlchemyProcessingResultOutboxRepository(db),
            batch_size=50,
            max_cycles=3,
        ).reconcile_once(
            enabled=True,
            batch_size=50,
            max_cycles=3,
            now=now,
        )
        second_claim = SqlAlchemyProcessingResultOutboxRepository(db).requeue_failed(
            event.id,
            now=now,
            max_cycles=3,
        )
        saved = db.query(models.ProcessingOutboxEvent).filter_by(id=event.id).one()

        self.assertEqual(first.requeued, 1)
        self.assertFalse(second_claim)
        self.assertEqual(saved.status, "pending")
        self.assertEqual(saved.attempt_count, 0)
        self.assertEqual(saved.recovery_cycle_count, 1)
        self.assertEqual(saved.id, "event-1")
        self.assertEqual(saved.event_key, "asset-1")
        self.assertEqual(saved.payload, original_payload)
        db.close()

    def test_recovery_disabled_does_not_query_or_requeue(self) -> None:
        db = MagicMock()
        result = ReconcileFailedProcessingResultsApplicationService(
            repository=SqlAlchemyProcessingResultOutboxRepository(db),
            batch_size=50,
            max_cycles=3,
        ).reconcile_once(enabled=False)
        self.assertTrue(result.disabled)
        db.query.assert_not_called()

    def test_final_transient_failure_after_max_cycle_becomes_recovery_exhausted(self) -> None:
        db = self.Session()
        event = new_event()
        event.status = "publishing"
        event.attempt_count = 4
        event.recovery_cycle_count = 3
        db.add(event)
        db.commit()
        classification = PublicationFailureClassification(
            PublicationFailureDisposition.TRANSIENT,
            "kafka_retryable_failure",
        )

        self.assertFalse(
            SqlAlchemyProcessingResultOutboxRepository(db).record_publication_failure(
                event.id,
                classification=classification,
                now=now_utc(),
                max_attempts=5,
                retry_delay_seconds=60,
                recovery_max_cycles=3,
                recovery_cooldown_seconds=60,
            )
        )

        saved = db.query(models.ProcessingOutboxEvent).filter_by(id=event.id).one()
        self.assertEqual(saved.failure_disposition, PublicationFailureDisposition.RECOVERY_EXHAUSTED.value)
        self.assertIsNone(saved.next_recovery_at)
        self.assertIsNotNone(saved.recovery_exhausted_at)
        db.close()

    def test_manual_one_shot_relay_still_uses_existing_publication_path(self) -> None:
        class Publisher:
            def __init__(self) -> None:
                self.ids: list[str] = []

            def publish(self, event) -> None:
                self.ids.append(event.id)

        db = self.Session()
        event = new_event()
        db.add(event)
        db.commit()
        publisher = Publisher()

        result = RelayProcessingResultsApplicationService(
            repository=SqlAlchemyProcessingResultOutboxRepository(db),
            publisher=publisher,
            policy=ProcessingResultRelayPolicy(10, 5, 60, 3, 60),
        ).relay_once(enabled=True, batch_size=1)

        self.assertEqual(result.published, 1)
        self.assertEqual(publisher.ids, [event.id])
        self.assertEqual(db.query(models.ProcessingOutboxEvent).filter_by(id=event.id).one().status, "published")
        db.close()


class AutomaticRelayRecoveryOwnershipTest(unittest.TestCase):
    def test_iteration_reconciles_before_normal_relay(self) -> None:
        order: list[str] = []
        recovery_result = MagicMock(eligible=1, requeued=1, skipped=0)
        relay_result = MagicMock()
        recovery_service = MagicMock()
        recovery_service.reconcile_once.side_effect = lambda **_kwargs: order.append("recovery") or recovery_result
        relay_service = MagicMock()
        relay_service.relay_once.side_effect = lambda **_kwargs: order.append("relay") or relay_result
        with (
            patch.object(
                processing_outbox_auto_relay,
                "build_result_reconciliation_service",
                return_value=recovery_service,
            ),
            patch.object(
                processing_outbox_auto_relay,
                "build_result_relay_service",
                return_value=relay_service,
            ),
        ):
            actual_recovery, actual_relay = processing_outbox_auto_relay._run_iteration(
                MagicMock(),
                MagicMock(),
                run_recovery=True,
            )

        self.assertEqual(order, ["recovery", "relay"])
        self.assertIs(actual_recovery, recovery_result)
        self.assertIs(actual_relay, relay_result)

    def test_iteration_skips_reconciliation_when_disabled_for_that_interval(self) -> None:
        relay_service = MagicMock()
        relay_service.relay_once.return_value = MagicMock()
        with (
            patch.object(processing_outbox_auto_relay, "build_result_reconciliation_service") as recovery,
            patch.object(
                processing_outbox_auto_relay,
                "build_result_relay_service",
                return_value=relay_service,
            ),
        ):
            processing_outbox_auto_relay._run_iteration(MagicMock(), MagicMock(), run_recovery=False)
        recovery.assert_not_called()

    def test_reconciliation_failure_is_safe_and_does_not_skip_normal_relay(self) -> None:
        db = MagicMock()
        relay_result = MagicMock()
        recovery_service = MagicMock()
        recovery_service.reconcile_once.side_effect = RuntimeError("private detail")
        relay_service = MagicMock()
        relay_service.relay_once.return_value = relay_result
        with (
            patch.object(
                processing_outbox_auto_relay,
                "build_result_reconciliation_service",
                return_value=recovery_service,
            ),
            patch.object(
                processing_outbox_auto_relay,
                "build_result_relay_service",
                return_value=relay_service,
            ) as relay,
            self.assertLogs(processing_outbox_auto_relay.logger, level="WARNING") as captured,
        ):
            recovery_result, actual_relay = processing_outbox_auto_relay._run_iteration(
                db,
                MagicMock(),
                run_recovery=True,
            )

        self.assertIsNone(recovery_result)
        self.assertIs(actual_relay, relay_result)
        db.rollback.assert_called_once_with()
        relay.assert_called_once()
        self.assertNotIn("private detail", " ".join(captured.output))


class ProcessingOutboxRecoverySchemaTest(unittest.TestCase):
    def test_existing_failed_rows_are_backfilled_unknown_without_replay_eligibility(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text(
                """
                CREATE TABLE processing_outbox_events (
                    id VARCHAR(64) PRIMARY KEY,
                    status VARCHAR(50) NOT NULL,
                    last_error TEXT,
                    created_at TIMESTAMP
                )
                """
            ))
            connection.execute(text(
                "INSERT INTO processing_outbox_events (id, status, created_at) "
                "VALUES ('historical', 'failed', CURRENT_TIMESTAMP)"
            ))

        ensure_processing_outbox_recovery_schema(engine)

        columns = {column["name"] for column in inspect(engine).get_columns("processing_outbox_events")}
        self.assertTrue({
            "failure_disposition",
            "recovery_cycle_count",
            "next_recovery_at",
            "last_failure_category",
            "recovery_exhausted_at",
        }.issubset(columns))
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT failure_disposition, recovery_cycle_count, next_recovery_at, "
                "last_failure_category, last_error "
                "FROM processing_outbox_events WHERE id='historical'"
            )).mappings().one()
        self.assertEqual(row["failure_disposition"], PublicationFailureDisposition.UNKNOWN.value)
        self.assertEqual(row["recovery_cycle_count"], 0)
        self.assertIsNone(row["next_recovery_at"])
        self.assertEqual(row["last_failure_category"], "historical_unclassified")
        self.assertEqual(row["last_error"], "historical_unclassified")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
