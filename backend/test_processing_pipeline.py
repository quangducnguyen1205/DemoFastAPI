import subprocess
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry

from app.config.settings import settings
from app.core.celery_app import celery_app
from app.consumers.asset_processing_consumer import handle_asset_processing_message
from app.events.asset_processing import EventValidationError, parse_asset_processing_requested_event
from app.processing.adapters.celery_dispatcher import (
    CeleryProcessingTaskDispatcher,
    encode_processing_task_payload,
)
from app.processing.adapters.whisper_transcriber import WhisperProcessingTranscriptionProvider
from app.processing.application.dispatch import DispatchProcessingApplicationService
from app.processing.application.execute import (
    ExecuteDirectUploadProcessingApplicationService,
    ExecuteProcessingApplicationService,
)
from app.processing.domain.models import (
    ProcessingClaimConflict,
    ProcessingExecutionCommand,
    ProcessingFailed,
    ProcessingFailure,
    ProcessingLease,
    ProcessingRequestCommand,
    ProcessingSkipped,
    ProcessingSucceeded,
    ProcessingTranscriptRow,
)
from app.processing.ports.request_repository import ProcessingRequestState
from app.processing.ports.task_dispatcher import ProcessingDispatch
from app.tasks.video_tasks import (
    _lease_retry_delay_seconds,
    process_asset_object_task,
    process_video_task,
)


def request_event() -> dict:
    return {
        "eventId": "event-1",
        "eventType": "asset.processing.requested",
        "eventVersion": 1,
        "aggregateType": "ASSET",
        "aggregateId": "asset-1",
        "occurredAt": "2026-07-13T00:00:00Z",
        "payload": {
            "assetId": "asset-1",
            "workspaceId": None,
            "ownerId": None,
            "storageBucket": "workspace-media",
            "objectKey": "objects/media.mp4",
            "originalFilename": None,
            "contentType": "video/mp4",
            "sizeBytes": 128,
            "requestedAt": None,
        },
    }


def command() -> ProcessingExecutionCommand:
    return parse_asset_processing_requested_event(request_event()).to_processing_command().to_execution_command()


class RequestIngestionBoundaryTest(unittest.TestCase):
    def test_parser_maps_the_frozen_envelope_and_nulls_to_a_neutral_command(self) -> None:
        event = parse_asset_processing_requested_event(request_event())
        actual = event.to_processing_command()
        self.assertIsInstance(actual, ProcessingRequestCommand)
        self.assertEqual(actual.event_id, "event-1")
        self.assertEqual(actual.aggregate_type, "ASSET")
        self.assertEqual(actual.storage_bucket, "workspace-media")
        self.assertIsNone(actual.workspace_id)
        self.assertIsNone(actual.original_filename)
        self.assertIsNone(actual.requested_at)

    def test_malformed_json_and_unsupported_type_are_rejected(self) -> None:
        with self.assertRaises(EventValidationError):
            parse_asset_processing_requested_event(b"not-json")
        unsupported = request_event()
        unsupported["eventType"] = "asset.processing.changed"
        with self.assertRaises(EventValidationError):
            parse_asset_processing_requested_event(unsupported)

    def test_consumer_adapter_delegates_only_the_neutral_command(self) -> None:
        service = MagicMock()
        service.dispatch.return_value = SimpleNamespace(
            accepted=True,
            duplicate=False,
            event_id="event-1",
            task_id="asset-processing-event-1",
        )
        with patch(
            "app.consumers.asset_processing_consumer.build_processing_dispatch_service",
            return_value=service,
        ):
            result = handle_asset_processing_message(request_event(), MagicMock())
        dispatched = service.dispatch.call_args.args[0]
        self.assertIsInstance(dispatched, ProcessingRequestCommand)
        self.assertTrue(result.accepted)
        self.assertFalse(result.rejected)


class DispatchApplicationServiceTest(unittest.TestCase):
    def test_new_request_dispatches_the_exact_payload_and_deterministic_task_id(self) -> None:
        enqueue = MagicMock(return_value=SimpleNamespace(id="asset-processing-event-1"))
        dispatcher = CeleryProcessingTaskDispatcher(enqueue)
        dispatched = dispatcher.dispatch(command())
        enqueue.assert_called_once_with(
            args=[
                {
                    "eventId": "event-1",
                    "assetId": "asset-1",
                    "workspaceId": None,
                    "ownerId": None,
                    "bucket": "workspace-media",
                    "objectKey": "objects/media.mp4",
                    "contentType": "video/mp4",
                    "originalFilename": None,
                    "sizeBytes": 128,
                }
            ],
            task_id="asset-processing-event-1",
        )
        self.assertEqual(dispatched.task_id, "asset-processing-event-1")

    def test_completed_duplicate_does_not_dispatch(self) -> None:
        repository = MagicMock()
        repository.get_or_create.return_value = ProcessingRequestState(
            "event-1", "asset-1", "ready", "existing-task", "workspace-media", "objects/media.mp4"
        )
        dispatcher = MagicMock()
        service = DispatchProcessingApplicationService(repository=repository, dispatcher=dispatcher)
        result = service.dispatch(parse_asset_processing_requested_event(request_event()).to_processing_command())
        self.assertTrue(result.duplicate)
        self.assertEqual(result.task_id, "existing-task")
        dispatcher.dispatch.assert_not_called()


class ExecuteProcessingApplicationServiceTest(unittest.TestCase):
    def build_service(self, *, segments=None, failure=None, status=None):
        store = MagicMock()
        sink = MagicMock()
        transcriber = MagicMock()
        if failure is None:
            transcriber.transcribe.return_value = segments or (
                ProcessingTranscriptRow(0, "first", 0, 1250),
                ProcessingTranscriptRow(1, "second", 1250, 2500),
            )
        else:
            transcriber.transcribe.side_effect = failure

        class MediaSource:
            @contextmanager
            def acquire(self, _command):
                yield "/tmp/media.mp4"

        fixed_now = datetime(2026, 7, 13, tzinfo=UTC)
        store.claim.return_value = (
            ProcessingClaimConflict(status)
            if status is not None
            else ProcessingLease(1, fixed_now, fixed_now + timedelta(hours=4))
        )
        service = ExecuteProcessingApplicationService(
            media_source=MediaSource(),
            transcriber=transcriber,
            artifact_store=store,
            result_sink=sink,
            clock=lambda: fixed_now,
        )
        return service, store, sink, transcriber

    def test_success_executes_linearly_and_records_one_canonical_outcome(self) -> None:
        service, store, sink, transcriber = self.build_service()
        outcome = service.execute(command(), task_id="task-1")
        self.assertIsInstance(outcome, ProcessingSucceeded)
        self.assertEqual([row.segment_index for row in outcome.artifact.rows], [0, 1])
        self.assertEqual([row.text for row in outcome.artifact.rows], ["first", "second"])
        self.assertEqual([row.start_ms for row in outcome.artifact.rows], [0, 1250])
        transcriber.transcribe.assert_called_once()
        store.persist_success.assert_called_once_with(outcome, attempt_count=1)
        sink.record.assert_called_once_with(outcome)
        store.commit.assert_called_once_with()

    def test_provider_failure_rolls_back_and_records_one_failed_outcome(self) -> None:
        error = RuntimeError("provider unavailable")
        service, store, sink, _ = self.build_service(failure=error)
        outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingFailed)
        self.assertEqual(outcome.failure.diagnostic_message, "provider unavailable")
        store.rollback.assert_called_once_with()
        store.persist_failure.assert_called_once_with(outcome, attempt_count=1)
        sink.record.assert_called_once_with(outcome)
        store.commit.assert_called_once_with()

    def test_already_processed_request_skips_media_and_result_recording(self) -> None:
        service, store, sink, transcriber = self.build_service(status="ready")
        outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingSkipped)
        transcriber.transcribe.assert_not_called()
        sink.record.assert_not_called()
        store.commit.assert_not_called()

    def test_active_processing_lease_skips_external_processing(self) -> None:
        service, store, sink, transcriber = self.build_service()
        retry_at = datetime(2026, 7, 13, tzinfo=UTC) + timedelta(hours=4)
        store.claim.return_value = ProcessingClaimConflict(
            "processing",
            lease_expires_at=retry_at,
        )
        outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingSkipped)
        self.assertEqual(outcome.status, "processing")
        self.assertEqual(outcome.retry_at, retry_at)
        transcriber.transcribe.assert_not_called()
        sink.record.assert_not_called()
        store.commit.assert_not_called()


class WhisperFailurePropagationTest(unittest.TestCase):
    """A failure inside the real Whisper transcription stage must reach the controlled
    failure path instead of being converted into an empty successful transcript."""

    def build_real_provider_service(self):
        store = MagicMock()
        sink = MagicMock()

        class MediaSource:
            @contextmanager
            def acquire(self, _command):
                yield "/tmp/media.mp4"

        fixed_now = datetime(2026, 7, 13, tzinfo=UTC)
        store.claim.return_value = ProcessingLease(1, fixed_now, fixed_now + timedelta(hours=4))
        service = ExecuteProcessingApplicationService(
            media_source=MediaSource(),
            transcriber=WhisperProcessingTranscriptionProvider(),
            artifact_store=store,
            result_sink=sink,
            clock=lambda: fixed_now,
        )
        return service, store, sink

    @contextmanager
    def whisper_model(self, model):
        with (
            patch(
                "app.processing.adapters.whisper_transcriber.extract_audio_to_wav",
                return_value="/tmp/audio.wav",
            ),
            patch("app.services.video_processing.get_whisper_model", return_value=model),
        ):
            yield

    def test_whisper_internal_exception_reaches_the_controlled_failure_path(self) -> None:
        service, store, sink = self.build_real_provider_service()
        model = MagicMock()
        model.transcribe.side_effect = RuntimeError("whisper backend crashed")
        with self.whisper_model(model):
            outcome = service.execute(command(), task_id="task-1")
        self.assertIsInstance(outcome, ProcessingFailed)
        self.assertEqual(outcome.failure.code, "PROCESSING_FAILED")
        store.rollback.assert_called_once_with()
        store.persist_failure.assert_called_once_with(outcome, attempt_count=1)
        sink.record.assert_called_once_with(outcome)
        store.commit.assert_called_once_with()

    def test_whisper_internal_exception_cannot_become_an_empty_success(self) -> None:
        service, store, sink = self.build_real_provider_service()
        model = MagicMock()
        model.transcribe.side_effect = RuntimeError("whisper backend crashed")
        with self.whisper_model(model):
            outcome = service.execute(command())
        self.assertNotIsInstance(outcome, ProcessingSucceeded)
        store.persist_success.assert_not_called()
        recorded = [call.args[0] for call in sink.record.call_args_list]
        self.assertFalse(any(isinstance(result, ProcessingSucceeded) for result in recorded))

    def test_normal_whisper_success_still_succeeds_through_the_real_provider(self) -> None:
        service, store, sink = self.build_real_provider_service()
        model = MagicMock()
        model.transcribe.return_value = {
            "text": "first second",
            "segments": [
                {"text": " first ", "start": 0.0, "end": 1.25},
                {"text": "second", "start": 1.25, "end": 2.5},
            ],
        }
        with self.whisper_model(model):
            outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingSucceeded)
        self.assertEqual([row.text for row in outcome.artifact.rows], ["first", "second"])
        store.persist_success.assert_called_once_with(outcome, attempt_count=1)
        sink.record.assert_called_once_with(outcome)

    def test_successful_zero_segment_transcription_remains_a_success(self) -> None:
        service, store, sink = self.build_real_provider_service()
        model = MagicMock()
        model.transcribe.return_value = {"text": "", "segments": []}
        with self.whisper_model(model):
            outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingSucceeded)
        self.assertEqual(outcome.artifact.segment_count, 0)
        store.persist_success.assert_called_once_with(outcome, attempt_count=1)
        sink.record.assert_called_once_with(outcome)

    def test_preprocessing_failure_still_fails_before_whisper_runs(self) -> None:
        service, store, sink = self.build_real_provider_service()
        model = MagicMock()
        with (
            patch(
                "app.processing.adapters.whisper_transcriber.extract_audio_to_wav",
                side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
            ),
            patch("app.services.video_processing.get_whisper_model", return_value=model),
        ):
            outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingFailed)
        self.assertEqual(outcome.failure.code, "PROCESSING_FAILED")
        model.transcribe.assert_not_called()
        store.persist_failure.assert_called_once_with(outcome, attempt_count=1)
        sink.record.assert_called_once_with(outcome)

    def test_direct_upload_whisper_exception_marks_failed_not_ready(self) -> None:
        store = MagicMock()
        store.exists.return_value = True
        service = ExecuteDirectUploadProcessingApplicationService(
            transcriber=WhisperProcessingTranscriptionProvider(),
            artifact_store=store,
        )
        model = MagicMock()
        model.transcribe.side_effect = RuntimeError("whisper backend crashed")
        with self.whisper_model(model):
            result = service.execute(video_id=7, media_path="/tmp/media.mp4", task_id="task-1")
        self.assertEqual(result["status"], "failed")
        store.persist_ready.assert_not_called()
        store.persist_failed.assert_called_once_with(7)


class CeleryWorkerAdapterTest(unittest.TestCase):
    def test_task_names_and_worker_discovery_metadata_are_unchanged(self) -> None:
        self.assertEqual(process_video_task.name, "process_video")
        self.assertEqual(process_asset_object_task.name, "process_asset_object")

    def test_object_task_uses_retry_safe_delivery_without_changing_direct_upload(self) -> None:
        self.assertTrue(process_asset_object_task.acks_late)
        self.assertTrue(process_asset_object_task.acks_on_failure_or_timeout)
        self.assertTrue(process_asset_object_task.reject_on_worker_lost)
        self.assertIsNone(process_asset_object_task.max_retries)
        self.assertEqual(celery_app.conf.worker_prefetch_multiplier, 1)
        self.assertEqual(
            celery_app.conf.broker_transport_options["visibility_timeout"],
            settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            celery_app.conf.result_backend_transport_options["visibility_timeout"],
            settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            celery_app.conf.visibility_timeout,
            settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
        )
        self.assertLess(
            settings.PROCESSING_LEASE_RETRY_POLL_SECONDS,
            settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
        )
        self.assertFalse(bool(process_video_task.acks_late))

    def test_retry_poll_is_bounded_below_redis_visibility_timeout(self) -> None:
        now = datetime(2026, 7, 13, tzinfo=UTC)
        self.assertEqual(
            _lease_retry_delay_seconds(
                now + timedelta(hours=4),
                now=now,
                poll_seconds=300,
                visibility_timeout_seconds=3_600,
            ),
            300,
        )
        self.assertEqual(
            _lease_retry_delay_seconds(
                now + timedelta(seconds=120),
                now=now,
                poll_seconds=300,
                visibility_timeout_seconds=3_600,
            ),
            120,
        )
        self.assertEqual(
            _lease_retry_delay_seconds(
                now + timedelta(hours=4),
                now=now,
                poll_seconds=5_000,
                visibility_timeout_seconds=3_600,
            ),
            3_599,
        )

    def test_active_lease_redelivery_polls_without_external_processing(self) -> None:
        retry_at = datetime(2026, 7, 13, tzinfo=UTC) + timedelta(hours=4)
        service = MagicMock()
        service.execute.return_value = ProcessingSkipped(
            "event-1",
            "asset-1",
            "processing",
            retry_at=retry_at,
        )
        with (
            patch("app.tasks.video_tasks.build_processing_execution_service", return_value=service),
            patch("app.tasks.video_tasks._lease_retry_delay_seconds", return_value=300),
            patch.object(process_asset_object_task, "retry", side_effect=Retry()) as retry,
            self.assertRaises(Retry),
        ):
            process_asset_object_task.run(encode_processing_task_payload(command()))
        retry.assert_called_once_with(countdown=300)
        service.execute.assert_called_once()
        service.close.assert_called_once_with()

    def test_duplicate_delivery_polling_creates_only_one_successor_per_delivery(self) -> None:
        service = MagicMock()
        service.execute.return_value = ProcessingSkipped(
            "event-1",
            "asset-1",
            "processing",
            retry_at=datetime(2026, 7, 13, tzinfo=UTC) + timedelta(hours=4),
        )
        deliveries = 4
        with (
            patch("app.tasks.video_tasks.build_processing_execution_service", return_value=service),
            patch("app.tasks.video_tasks._lease_retry_delay_seconds", return_value=300),
            patch.object(process_asset_object_task, "retry", side_effect=Retry()) as retry,
        ):
            for _index in range(deliveries):
                with self.assertRaises(Retry):
                    process_asset_object_task.run(encode_processing_task_payload(command()))
        self.assertEqual(retry.call_count, deliveries)
        self.assertEqual(service.execute.call_count, deliveries)
        self.assertEqual(service.close.call_count, deliveries)
        self.assertTrue(
            all(call.kwargs == {"countdown": 300} for call in retry.call_args_list)
        )

    def test_active_lease_poll_then_expired_reclaim_reaches_success_once(self) -> None:
        active = ProcessingSkipped(
            "event-1",
            "asset-1",
            "processing",
            retry_at=datetime(2026, 7, 13, tzinfo=UTC) + timedelta(minutes=5),
        )
        recovered = ProcessingSucceeded(
            "event-1",
            "asset-1",
            SimpleNamespace(rows=(SimpleNamespace(text="recovered"),), segment_count=1),
            datetime(2026, 7, 13, tzinfo=UTC) + timedelta(minutes=5),
        )
        service = MagicMock()
        service.execute.side_effect = (active, recovered)
        with (
            patch("app.tasks.video_tasks.build_processing_execution_service", return_value=service),
            patch("app.tasks.video_tasks._lease_retry_delay_seconds", return_value=300),
            patch.object(process_asset_object_task, "retry", side_effect=Retry()) as retry,
        ):
            with self.assertRaises(Retry):
                process_asset_object_task.run(encode_processing_task_payload(command()))
            result = process_asset_object_task.run(encode_processing_task_payload(command()))
        self.assertEqual(
            result,
            {"status": "ready", "asset_id": "asset-1", "segments": ["recovered"]},
        )
        retry.assert_called_once_with(countdown=300)
        self.assertEqual(service.execute.call_count, 2)

    def test_controlled_terminal_failure_returns_without_celery_retry(self) -> None:
        error = RuntimeError("provider unavailable")
        outcome = ProcessingFailed(
            "event-1",
            "asset-1",
            ProcessingFailure("PROCESSING_FAILED", "provider unavailable", error),
            datetime(2026, 7, 13, tzinfo=UTC),
        )
        service = MagicMock()
        service.execute.return_value = outcome
        with (
            patch("app.tasks.video_tasks.build_processing_execution_service", return_value=service),
            patch.object(process_asset_object_task, "retry") as retry,
        ):
            result = process_asset_object_task.run(encode_processing_task_payload(command()))
        self.assertEqual(
            result,
            {
                "status": "failed",
                "asset_id": "asset-1",
                "error": "provider unavailable",
            },
        )
        retry.assert_not_called()
        service.close.assert_called_once_with()

    def test_asset_task_maps_command_and_success_without_owning_the_algorithm(self) -> None:
        outcome = ProcessingSucceeded(
            "event-1",
            "asset-1",
            SimpleNamespace(rows=(SimpleNamespace(text="first"),), segment_count=1),
            datetime(2026, 7, 13, tzinfo=UTC),
        )
        service = MagicMock()
        service.execute.return_value = outcome
        with patch("app.tasks.video_tasks.build_processing_execution_service", return_value=service):
            result = process_asset_object_task.run(encode_processing_task_payload(command()))
        passed_command = service.execute.call_args.args[0]
        self.assertEqual(passed_command, command())
        self.assertEqual(result, {"status": "ready", "asset_id": "asset-1", "segments": ["first"]})
        service.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
