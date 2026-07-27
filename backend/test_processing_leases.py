import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import models
from app.core import schema
from app.core.database import Base
from app.processing.adapters.sqlalchemy_stores import SqlAlchemyProcessingArtifactStore
from app.processing.application.execute import ExecuteProcessingApplicationService
from app.processing.domain.models import (
    ProcessingArtifact,
    ProcessingClaimConflict,
    ProcessingExecutionCommand,
    ProcessingFailed,
    ProcessingLease,
    ProcessingLeaseLost,
    ProcessingSkipped,
    ProcessingSucceeded,
    ProcessingTranscriptRow,
)
from app.result_delivery.adapters.sqlalchemy_repository import (
    SqlAlchemyProcessingResultOutboxRepository,
)
from app.result_delivery.application.record_result import (
    RecordProcessingResultApplicationService,
)


NOW = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)
LEASE_SECONDS = 3_600


def command(event_id: str = "request-1", asset_id: str = "asset-1") -> ProcessingExecutionCommand:
    return ProcessingExecutionCommand(
        event_id=event_id,
        asset_id=asset_id,
        workspace_id="workspace-1",
        owner_id="owner-1",
        storage_bucket="workspace-media",
        object_key="objects/media.mp4",
        original_filename="media.mp4",
        content_type="video/mp4",
        size_bytes=128,
    )


def add_request(
    db,
    *,
    event_id: str = "request-1",
    asset_id: str = "asset-1",
    status: str = "accepted",
    attempt_count: int = 0,
    processing_started_at=None,
    lease_expires_at=None,
) -> models.ProcessingRequest:
    request = models.ProcessingRequest(
        event_id=event_id,
        asset_id=asset_id,
        workspace_id="workspace-1",
        owner_id="owner-1",
        storage_bucket="workspace-media",
        object_key="objects/media.mp4",
        original_filename="media.mp4",
        content_type="video/mp4",
        size_bytes=128,
        status=status,
        attempt_count=attempt_count,
        processing_started_at=processing_started_at,
        lease_expires_at=lease_expires_at,
    )
    db.add(request)
    db.commit()
    return request


class ProcessingLeaseSchemaTest(unittest.TestCase):
    def test_fresh_schema_contains_lease_columns_and_named_constraints(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            Base.metadata.create_all(bind=engine)
            columns = {
                column["name"]: column
                for column in inspect(engine).get_columns("processing_requests")
            }
            self.assertTrue(
                {
                    "processing_started_at",
                    "lease_expires_at",
                    "attempt_count",
                }.issubset(columns)
            )
            self.assertFalse(columns["attempt_count"]["nullable"])
            constraints = {
                constraint["name"]
                for constraint in inspect(engine).get_check_constraints(
                    "processing_requests"
                )
            }
            self.assertIn(
                "ck_processing_request_attempt_count_nonnegative",
                constraints,
            )
            self.assertIn("ck_processing_request_lease_shape", constraints)
        finally:
            engine.dispose()

    def test_existing_schema_is_upgraded_idempotently_and_legacy_rows_are_readable(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    """
                    CREATE TABLE processing_requests (
                        event_id VARCHAR(64) PRIMARY KEY,
                        asset_id VARCHAR(64) NOT NULL,
                        workspace_id VARCHAR(64),
                        owner_id VARCHAR(255),
                        storage_bucket VARCHAR(255) NOT NULL,
                        object_key VARCHAR(1024) NOT NULL,
                        original_filename VARCHAR(500),
                        content_type VARCHAR(255) NOT NULL,
                        size_bytes BIGINT NOT NULL,
                        celery_task_id VARCHAR(255),
                        status VARCHAR(50) NOT NULL,
                        segment_count INTEGER,
                        error TEXT,
                        occurred_at VARCHAR(64),
                        requested_at VARCHAR(64),
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
                    )
                    """
                ))
                for event_id, status in (
                    ("legacy-accepted", "accepted"),
                    ("legacy-processing", "processing"),
                ):
                    connection.execute(
                        text(
                            """
                            INSERT INTO processing_requests (
                                event_id, asset_id, storage_bucket, object_key,
                                content_type, size_bytes, status
                            ) VALUES (
                                :event_id, 'asset-1', 'workspace-media',
                                'objects/media.mp4', 'video/mp4', 128, :status
                            )
                            """
                        ),
                        {"event_id": event_id, "status": status},
                    )

            schema.ensure_processing_request_lease_schema(engine)
            schema.ensure_processing_request_lease_schema(engine)

            columns = {
                column["name"]
                for column in inspect(engine).get_columns("processing_requests")
            }
            self.assertTrue(
                {
                    "processing_started_at",
                    "lease_expires_at",
                    "attempt_count",
                }.issubset(columns)
            )
            Session = sessionmaker(bind=engine)
            db = Session()
            try:
                accepted = db.query(models.ProcessingRequest).filter_by(
                    event_id="legacy-accepted"
                ).one()
                abandoned = db.query(models.ProcessingRequest).filter_by(
                    event_id="legacy-processing"
                ).one()
                self.assertEqual(accepted.attempt_count, 0)
                self.assertIsNone(accepted.processing_started_at)
                self.assertIsNone(accepted.lease_expires_at)
                self.assertEqual(abandoned.attempt_count, 0)
                self.assertIsNone(abandoned.processing_started_at)
                self.assertIsNotNone(abandoned.lease_expires_at)
            finally:
                db.close()
        finally:
            engine.dispose()

    def test_attempt_count_cannot_be_negative(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            db.add(models.ProcessingRequest(
                event_id="negative-attempt",
                asset_id="asset-1",
                storage_bucket="workspace-media",
                object_key="objects/media.mp4",
                content_type="video/mp4",
                size_bytes=128,
                status="accepted",
                attempt_count=-1,
            ))
            with self.assertRaises(IntegrityError):
                db.commit()
        finally:
            db.rollback()
            db.close()
            engine.dispose()

    def test_postgresql_upgrade_scopes_constraints_to_the_current_schema(self) -> None:
        connection = MagicMock()
        schema._apply_processing_request_lease_schema(
            connection,
            "postgresql",
            {
                "processing_started_at",
                "lease_expires_at",
                "attempt_count",
            },
        )
        constraint_sql = next(
            str(call.args[0])
            for call in connection.execute.call_args_list
            if "ck_processing_request_attempt_count_nonnegative" in str(call.args[0])
        )
        self.assertIn("table_record.relname = 'processing_requests'", constraint_sql)
        self.assertIn("schema_record.nspname = current_schema()", constraint_sql)
        self.assertIn("ck_processing_request_lease_shape", constraint_sql)


class ProcessingLeaseClaimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_accepted_request_claim_sets_timestamps_and_increments_attempt(self) -> None:
        db = self.Session()
        add_request(db, status="accepted")
        store = SqlAlchemyProcessingArtifactStore(db, lease_seconds=LEASE_SECONDS)
        claim = store.claim(command(), now=NOW)
        self.assertIsInstance(claim, ProcessingLease)
        self.assertEqual(claim.attempt_count, 1)
        self.assertEqual(claim.processing_started_at, NOW.replace(tzinfo=None))
        self.assertEqual(
            claim.lease_expires_at,
            (NOW + timedelta(seconds=LEASE_SECONDS)).replace(tzinfo=None),
        )
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.status, "processing")
        db.close()

    def test_enqueued_request_can_be_claimed(self) -> None:
        db = self.Session()
        add_request(db, status="enqueued")
        claim = SqlAlchemyProcessingArtifactStore(
            db,
            lease_seconds=LEASE_SECONDS,
        ).claim(command(), now=NOW)
        self.assertIsInstance(claim, ProcessingLease)
        self.assertEqual(claim.attempt_count, 1)
        db.close()

    def test_active_lease_cannot_be_claimed_or_mutated_again(self) -> None:
        db = self.Session()
        add_request(db, status="enqueued")
        store = SqlAlchemyProcessingArtifactStore(db, lease_seconds=LEASE_SECONDS)
        first = store.claim(command(), now=NOW)
        second = store.claim(command(), now=NOW + timedelta(seconds=10))
        self.assertIsInstance(first, ProcessingLease)
        self.assertIsInstance(second, ProcessingClaimConflict)
        self.assertEqual(second.status, "processing")
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.attempt_count, 1)
        self.assertEqual(saved.processing_started_at, NOW.replace(tzinfo=None))
        self.assertEqual(saved.lease_expires_at, first.lease_expires_at)
        db.close()

    def test_expired_lease_is_reclaimed_and_increments_attempt(self) -> None:
        db = self.Session()
        add_request(db, status="accepted")
        store = SqlAlchemyProcessingArtifactStore(db, lease_seconds=LEASE_SECONDS)
        first = store.claim(command(), now=NOW)
        reclaimed_at = NOW + timedelta(seconds=LEASE_SECONDS)
        second = store.claim(command(), now=reclaimed_at)
        self.assertIsInstance(first, ProcessingLease)
        self.assertIsInstance(second, ProcessingLease)
        self.assertEqual(second.attempt_count, 2)
        self.assertEqual(second.processing_started_at, reclaimed_at.replace(tzinfo=None))
        db.close()

    def test_terminal_ready_and_failed_requests_cannot_be_claimed(self) -> None:
        for index, status in enumerate(("ready", "failed")):
            with self.subTest(status=status):
                db = self.Session()
                event_id = f"request-{index}"
                add_request(db, event_id=event_id, status=status)
                result = SqlAlchemyProcessingArtifactStore(
                    db,
                    lease_seconds=LEASE_SECONDS,
                ).claim(command(event_id=event_id), now=NOW)
                self.assertIsInstance(result, ProcessingClaimConflict)
                self.assertEqual(result.status, status)
                self.assertEqual(
                    db.query(models.ProcessingRequest).filter_by(
                        event_id=event_id
                    ).one().attempt_count,
                    0,
                )
                db.close()

    def test_two_competing_claims_produce_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "lease.db"
            engine = create_engine(
                f"sqlite+pysqlite:///{database_path}",
                connect_args={"check_same_thread": False, "timeout": 10},
            )
            Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine)
            setup_db = Session()
            add_request(setup_db, status="enqueued")
            setup_db.close()
            barrier = threading.Barrier(2)

            def claim_once():
                db = Session()
                try:
                    barrier.wait()
                    return SqlAlchemyProcessingArtifactStore(
                        db,
                        lease_seconds=LEASE_SECONDS,
                    ).claim(command(), now=NOW)
                finally:
                    db.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = tuple(executor.map(lambda _index: claim_once(), range(2)))

            self.assertEqual(
                sum(isinstance(result, ProcessingLease) for result in results),
                1,
            )
            self.assertEqual(
                sum(isinstance(result, ProcessingClaimConflict) for result in results),
                1,
            )
            verify_db = Session()
            self.assertEqual(
                verify_db.query(models.ProcessingRequest).one().attempt_count,
                1,
            )
            verify_db.close()
            engine.dispose()


class ProcessingLeaseCompletionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _service(self, db, *, now, rows=None, failure=None, transcriber=None):
        class MediaSource:
            @contextmanager
            def acquire(self, _command):
                yield "/tmp/media.mp4"

        if transcriber is None:
            class Transcriber:
                def transcribe(self, *_args, **_kwargs):
                    if failure is not None:
                        raise failure
                    return rows or (
                        ProcessingTranscriptRow(0, "first", 0, 1000),
                        ProcessingTranscriptRow(1, "second", 1000, 2000),
                    )

            transcriber = Transcriber()

        store = SqlAlchemyProcessingArtifactStore(
            db,
            lease_seconds=LEASE_SECONDS,
        )
        sink = RecordProcessingResultApplicationService(
            SqlAlchemyProcessingResultOutboxRepository(db),
            event_id_factory=lambda: "result-1",
        )
        return ExecuteProcessingApplicationService(
            media_source=MediaSource(),
            transcriber=transcriber,
            artifact_store=store,
            result_sink=sink,
            clock=lambda: now,
        )

    def test_success_clears_lease_and_commits_artifact_with_one_result_intent(self) -> None:
        db = self.Session()
        add_request(db, status="enqueued")
        outcome = self._service(db, now=NOW).execute(command())
        self.assertIsInstance(outcome, ProcessingSucceeded)
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.status, "ready")
        self.assertEqual(saved.attempt_count, 1)
        self.assertIsNone(saved.processing_started_at)
        self.assertIsNone(saved.lease_expires_at)
        self.assertEqual(saved.segment_count, 2)
        self.assertEqual(db.query(models.ProcessingRequestTranscript).count(), 2)
        outbox = db.query(models.ProcessingOutboxEvent).one()
        self.assertEqual(outbox.event_type, "transcript.ready")
        self.assertEqual(outbox.causation_event_id, "request-1")
        db.close()

    def test_controlled_failure_sanitizes_error_clears_lease_and_records_one_intent(self) -> None:
        db = self.Session()
        add_request(db, status="accepted")
        outcome = self._service(
            db,
            now=NOW,
            failure=RuntimeError("token=private-value provider unavailable"),
        ).execute(command())
        self.assertIsInstance(outcome, ProcessingFailed)
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.status, "failed")
        self.assertEqual(saved.attempt_count, 1)
        self.assertEqual(
            saved.error,
            "token=[redacted] provider unavailable",
        )
        self.assertIsNone(saved.processing_started_at)
        self.assertIsNone(saved.lease_expires_at)
        outbox = db.query(models.ProcessingOutboxEvent).one()
        self.assertEqual(outbox.event_type, "asset.processing.failed")
        self.assertEqual(
            outbox.payload["errorMessage"],
            "token=[redacted] provider unavailable",
        )
        db.close()

    def test_duplicate_success_execution_does_not_create_a_second_result_intent(self) -> None:
        db = self.Session()
        add_request(db, status="enqueued")
        service = self._service(db, now=NOW)
        first = service.execute(command())
        second = service.execute(command())
        self.assertIsInstance(first, ProcessingSucceeded)
        self.assertIsInstance(second, ProcessingSkipped)
        self.assertEqual(second.status, "ready")
        self.assertEqual(db.query(models.ProcessingOutboxEvent).count(), 1)
        db.close()

    def test_success_artifact_rolls_back_before_controlled_failure_result_is_committed(self) -> None:
        db = self.Session()
        add_request(db, status="accepted")
        actual_sink = RecordProcessingResultApplicationService(
            SqlAlchemyProcessingResultOutboxRepository(db),
            event_id_factory=lambda: "result-failed",
        )

        class FailFirstResultSink:
            def __init__(self):
                self.calls = 0

            def record(self, outcome):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("result persistence unavailable")
                return actual_sink.record(outcome)

        service = self._service(db, now=NOW)
        service._result_sink = FailFirstResultSink()
        outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingFailed)
        self.assertEqual(db.query(models.ProcessingRequestTranscript).count(), 0)
        request = db.query(models.ProcessingRequest).one()
        self.assertEqual(request.status, "failed")
        self.assertIsNone(request.processing_started_at)
        self.assertIsNone(request.lease_expires_at)
        outbox = db.query(models.ProcessingOutboxEvent).one()
        self.assertEqual(outbox.event_type, "asset.processing.failed")
        db.close()

    def test_crash_then_expiry_reclaim_replaces_incomplete_artifacts_and_reaches_ready(self) -> None:
        db = self.Session()
        add_request(db, status="enqueued")
        store = SqlAlchemyProcessingArtifactStore(db, lease_seconds=LEASE_SECONDS)
        abandoned = store.claim(command(), now=NOW)
        self.assertIsInstance(abandoned, ProcessingLease)
        db.add(models.ProcessingRequestTranscript(
            processing_request_event_id="request-1",
            segment_index=0,
            text="incomplete attempt",
        ))
        db.commit()

        active_result = self._service(
            db,
            now=NOW + timedelta(seconds=10),
        ).execute(command())
        self.assertIsInstance(active_result, ProcessingSkipped)
        self.assertEqual(active_result.status, "processing")

        retry_at = NOW + timedelta(seconds=LEASE_SECONDS)
        recovered = self._service(
            db,
            now=retry_at,
            rows=(ProcessingTranscriptRow(0, "recovered", 0, 1000),),
        ).execute(command())
        self.assertIsInstance(recovered, ProcessingSucceeded)
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.status, "ready")
        self.assertEqual(saved.attempt_count, 2)
        self.assertEqual(saved.asset_id, "asset-1")
        self.assertEqual(saved.event_id, "request-1")
        self.assertIsNone(saved.processing_started_at)
        self.assertIsNone(saved.lease_expires_at)
        rows = db.query(models.ProcessingRequestTranscript).all()
        self.assertEqual([(row.segment_index, row.text) for row in rows], [(0, "recovered")])
        outbox = db.query(models.ProcessingOutboxEvent).all()
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].causation_event_id, "request-1")
        self.assertEqual(outbox[0].aggregate_id, "asset-1")
        db.close()

    def test_superseded_attempt_is_fenced_from_terminal_persistence(self) -> None:
        db = self.Session()
        add_request(db, status="accepted")
        store = SqlAlchemyProcessingArtifactStore(db, lease_seconds=LEASE_SECONDS)
        first = store.claim(command(), now=NOW)
        second = store.claim(
            command(),
            now=NOW + timedelta(seconds=LEASE_SECONDS),
        )
        self.assertIsInstance(first, ProcessingLease)
        self.assertIsInstance(second, ProcessingLease)
        stale_outcome = ProcessingSucceeded(
            "request-1",
            "asset-1",
            ProcessingArtifact((ProcessingTranscriptRow(0, "stale"),)),
            NOW + timedelta(seconds=LEASE_SECONDS + 1),
        )
        with self.assertRaises(ProcessingLeaseLost):
            store.persist_success(stale_outcome, attempt_count=first.attempt_count)
        store.rollback()
        store.persist_success(stale_outcome, attempt_count=second.attempt_count)
        RecordProcessingResultApplicationService(
            SqlAlchemyProcessingResultOutboxRepository(db),
            event_id_factory=lambda: "result-1",
        ).record(stale_outcome)
        store.commit()
        self.assertEqual(db.query(models.ProcessingOutboxEvent).count(), 1)
        self.assertEqual(db.query(models.ProcessingRequest).one().attempt_count, 2)
        db.close()


if __name__ == "__main__":
    unittest.main()
