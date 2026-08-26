from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.core.database import Base
from app.processing.adapters.celery_dispatcher import (
    encode_youtube_processing_task_payload,
)
from app.processing.adapters.sqlalchemy_stores import (
    SqlAlchemyProcessingArtifactStore,
)
from app.processing.application.execute import ExecuteProcessingApplicationService
from app.processing.domain.failures import (
    YOUTUBE_ACQUISITION_FAILED,
    YOUTUBE_UNAVAILABLE,
    YouTubePoTokenProviderUnavailableError,
    YouTubeUnavailableError,
)
from app.processing.domain.models import (
    ProcessingArtifact,
    ProcessingFailed,
    ProcessingFailure,
    ProcessingLease,
    ProcessingLeaseLost,
    ProcessingSkipped,
    ProcessingSucceeded,
    ProcessingTranscriptRow,
    YouTubeProcessingExecutionCommand,
)
from app.result_delivery.adapters.sqlalchemy_repository import (
    SqlAlchemyProcessingResultOutboxRepository,
)
from app.result_delivery.application.record_result import (
    RecordProcessingResultApplicationService,
)
from app.tasks.video_tasks import process_youtube_asset_task


NOW = datetime(2026, 7, 27, tzinfo=UTC)
LEASE_SECONDS = 60


def command() -> YouTubeProcessingExecutionCommand:
    return YouTubeProcessingExecutionCommand(
        event_id="youtube-request-1",
        asset_id="asset-youtube-1",
        workspace_id="workspace-1",
        owner_id="owner-1",
        youtube_video_id="abc_DEF-123",
    )


def add_request(db, *, status: str = "enqueued") -> models.ProcessingRequest:
    request = models.ProcessingRequest(
        event_id="youtube-request-1",
        asset_id="asset-youtube-1",
        workspace_id="workspace-1",
        owner_id="owner-1",
        source_type="YOUTUBE",
        youtube_video_id="abc_DEF-123",
        storage_bucket=None,
        object_key=None,
        original_filename=None,
        content_type=None,
        size_bytes=None,
        status=status,
    )
    db.add(request)
    db.commit()
    return request


class TrackingMediaSource:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.acquire_count = 0

    @contextmanager
    def acquire(self, _command):
        self.acquire_count += 1
        if self.failure is not None:
            raise self.failure
        yield "/tmp/youtube-owned/media.webm"


class FixedTranscriber:
    def transcribe(self, *_args, **_kwargs):
        return (
            ProcessingTranscriptRow(0, "first", 0, 1_250),
            ProcessingTranscriptRow(1, "second", 1_250, 2_500),
        )


class YouTubeExecutionLeaseIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _service(self, db, *, now: datetime, media_source: TrackingMediaSource):
        return ExecuteProcessingApplicationService(
            media_source=media_source,
            transcriber=FixedTranscriber(),
            artifact_store=SqlAlchemyProcessingArtifactStore(
                db,
                lease_seconds=LEASE_SECONDS,
            ),
            result_sink=RecordProcessingResultApplicationService(
                SqlAlchemyProcessingResultOutboxRepository(db),
                event_id_factory=lambda: "youtube-result-1",
            ),
            clock=lambda: now,
        )

    def test_active_lease_does_not_download_or_mutate_attempt_count(self) -> None:
        db = self.Session()
        add_request(db)
        claimed = SqlAlchemyProcessingArtifactStore(
            db,
            lease_seconds=LEASE_SECONDS,
        ).claim(command(), now=NOW)
        self.assertIsInstance(claimed, ProcessingLease)

        media_source = TrackingMediaSource()
        outcome = self._service(
            db,
            now=NOW + timedelta(seconds=1),
            media_source=media_source,
        ).execute(command())
        self.assertIsInstance(outcome, ProcessingSkipped)
        self.assertEqual(outcome.status, "processing")
        self.assertEqual(media_source.acquire_count, 0)
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.status, "processing")
        self.assertEqual(saved.attempt_count, 1)
        self.assertEqual(db.query(models.ProcessingOutboxEvent).count(), 0)
        db.close()

    def test_worker_loss_expiry_reclaim_reaches_one_v1_ready_result(self) -> None:
        db = self.Session()
        add_request(db)
        store = SqlAlchemyProcessingArtifactStore(db, lease_seconds=LEASE_SECONDS)
        abandoned = store.claim(command(), now=NOW)
        self.assertIsInstance(abandoned, ProcessingLease)
        db.add(models.ProcessingRequestTranscript(
            processing_request_event_id="youtube-request-1",
            segment_index=0,
            text="incomplete attempt",
        ))
        db.commit()

        media_source = TrackingMediaSource()
        outcome = self._service(
            db,
            now=NOW + timedelta(seconds=LEASE_SECONDS),
            media_source=media_source,
        ).execute(command(), task_id="youtube-task-2")
        self.assertIsInstance(outcome, ProcessingSucceeded)
        self.assertEqual(media_source.acquire_count, 1)
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.status, "ready")
        self.assertEqual(saved.attempt_count, 2)
        self.assertIsNone(saved.processing_started_at)
        self.assertIsNone(saved.lease_expires_at)
        rows = db.query(models.ProcessingRequestTranscript).all()
        self.assertEqual(
            [(row.segment_index, row.text, row.start_ms, row.end_ms) for row in rows],
            [
                (0, "first", 0, 1_250),
                (1, "second", 1_250, 2_500),
            ],
        )
        outbox = db.query(models.ProcessingOutboxEvent).one()
        self.assertEqual(outbox.event_type, "transcript.ready")
        self.assertEqual(outbox.event_version, 1)
        self.assertEqual(outbox.causation_event_id, "youtube-request-1")
        self.assertEqual(outbox.aggregate_id, "asset-youtube-1")
        self.assertEqual(outbox.payload["status"], "ready")
        self.assertEqual(outbox.payload["segmentCount"], 2)
        db.close()

    def test_controlled_acquisition_failure_is_terminal_with_one_v1_result(self) -> None:
        db = self.Session()
        add_request(db, status="accepted")
        outcome = self._service(
            db,
            now=NOW,
            media_source=TrackingMediaSource(failure=YouTubeUnavailableError()),
        ).execute(command())
        self.assertIsInstance(outcome, ProcessingFailed)
        self.assertEqual(outcome.failure.code, YOUTUBE_UNAVAILABLE)
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.status, "failed")
        self.assertEqual(saved.attempt_count, 1)
        self.assertIsNone(saved.processing_started_at)
        self.assertIsNone(saved.lease_expires_at)
        outbox = db.query(models.ProcessingOutboxEvent).one()
        self.assertEqual(outbox.event_type, "asset.processing.failed")
        self.assertEqual(outbox.event_version, 1)
        self.assertEqual(outbox.payload["errorCode"], YOUTUBE_UNAVAILABLE)
        self.assertEqual(
            outbox.payload["errorMessage"],
            "YouTube video is unavailable for public unauthenticated acquisition",
        )
        self.assertNotIn("http", outbox.payload["errorMessage"].lower())
        db.close()

    def test_provider_retry_exhaustion_records_one_generic_terminal_result(self) -> None:
        db = self.Session()
        add_request(db, status="accepted")
        media_source = TrackingMediaSource(
            failure=YouTubePoTokenProviderUnavailableError()
        )

        outcome = self._service(
            db,
            now=NOW,
            media_source=media_source,
        ).execute(command())

        self.assertIsInstance(outcome, ProcessingFailed)
        self.assertEqual(outcome.failure.code, YOUTUBE_ACQUISITION_FAILED)
        self.assertEqual(media_source.acquire_count, 1)
        self.assertEqual(db.query(models.ProcessingOutboxEvent).count(), 1)
        outbox = db.query(models.ProcessingOutboxEvent).one()
        self.assertEqual(outbox.payload["errorCode"], YOUTUBE_ACQUISITION_FAILED)
        self.assertEqual(
            outbox.payload["errorMessage"],
            "YouTube media acquisition failed",
        )
        db.close()

    def test_duplicate_success_does_not_create_another_result_intent(self) -> None:
        db = self.Session()
        add_request(db)
        media_source = TrackingMediaSource()
        service = self._service(db, now=NOW, media_source=media_source)
        first = service.execute(command())
        second = service.execute(command())
        self.assertIsInstance(first, ProcessingSucceeded)
        self.assertIsInstance(second, ProcessingSkipped)
        self.assertEqual(second.status, "ready")
        self.assertEqual(media_source.acquire_count, 1)
        self.assertEqual(db.query(models.ProcessingOutboxEvent).count(), 1)
        db.close()

    def test_attempt_fencing_rejects_stale_youtube_completion(self) -> None:
        db = self.Session()
        add_request(db)
        store = SqlAlchemyProcessingArtifactStore(db, lease_seconds=LEASE_SECONDS)
        first = store.claim(command(), now=NOW)
        second = store.claim(
            command(),
            now=NOW + timedelta(seconds=LEASE_SECONDS),
        )
        self.assertIsInstance(first, ProcessingLease)
        self.assertIsInstance(second, ProcessingLease)
        stale = ProcessingSucceeded(
            command().event_id,
            command().asset_id,
            ProcessingArtifact((ProcessingTranscriptRow(0, "stale"),)),
            NOW + timedelta(seconds=LEASE_SECONDS + 1),
        )
        with self.assertRaises(ProcessingLeaseLost):
            store.persist_success(stale, attempt_count=first.attempt_count)
        store.rollback()
        self.assertEqual(db.query(models.ProcessingRequest).one().status, "processing")
        self.assertEqual(db.query(models.ProcessingRequest).one().attempt_count, 2)
        db.close()


class YouTubeCeleryDeliveryTest(unittest.TestCase):
    def test_task_has_same_retry_safe_delivery_metadata_as_v1(self) -> None:
        self.assertEqual(process_youtube_asset_task.name, "process_youtube_asset")
        self.assertTrue(process_youtube_asset_task.acks_late)
        self.assertTrue(process_youtube_asset_task.acks_on_failure_or_timeout)
        self.assertTrue(process_youtube_asset_task.reject_on_worker_lost)
        self.assertIsNone(process_youtube_asset_task.max_retries)

    def test_active_poll_then_reclaim_executes_one_terminal_success(self) -> None:
        active = ProcessingSkipped(
            command().event_id,
            command().asset_id,
            "processing",
            retry_at=NOW + timedelta(minutes=5),
        )
        recovered = ProcessingSucceeded(
            command().event_id,
            command().asset_id,
            SimpleNamespace(
                rows=(SimpleNamespace(text="recovered"),),
                segment_count=1,
            ),
            NOW + timedelta(minutes=5),
        )
        service = MagicMock()
        service.execute.side_effect = (active, recovered)
        with (
            patch(
                "app.tasks.video_tasks.build_youtube_processing_execution_service",
                return_value=service,
            ),
            patch(
                "app.tasks.video_tasks._lease_retry_delay_seconds",
                return_value=300,
            ),
            patch.object(
                process_youtube_asset_task,
                "retry",
                side_effect=Retry(),
            ) as retry,
        ):
            with self.assertRaises(Retry):
                process_youtube_asset_task.run(
                    encode_youtube_processing_task_payload(command())
                )
            result = process_youtube_asset_task.run(
                encode_youtube_processing_task_payload(command())
            )
        retry.assert_called_once_with(countdown=300)
        self.assertEqual(service.execute.call_count, 2)
        self.assertEqual(
            result,
            {
                "status": "ready",
                "asset_id": "asset-youtube-1",
                "segments": ["recovered"],
            },
        )

    def test_controlled_failure_is_acknowledged_without_celery_retry(self) -> None:
        failure = ProcessingFailed(
            command().event_id,
            command().asset_id,
            ProcessingFailure(
                YOUTUBE_UNAVAILABLE,
                "YouTube video is unavailable for public unauthenticated acquisition",
                YouTubeUnavailableError(),
            ),
            NOW,
        )
        service = MagicMock()
        service.execute.return_value = failure
        with (
            patch(
                "app.tasks.video_tasks.build_youtube_processing_execution_service",
                return_value=service,
            ),
            patch.object(process_youtube_asset_task, "retry") as retry,
        ):
            result = process_youtube_asset_task.run(
                encode_youtube_processing_task_payload(command())
            )
        retry.assert_not_called()
        service.close.assert_called_once_with()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["error"],
            "YouTube video is unavailable for public unauthenticated acquisition",
        )


if __name__ == "__main__":
    unittest.main()
