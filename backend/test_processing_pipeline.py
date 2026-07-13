import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.consumers.asset_processing_consumer import handle_asset_processing_message
from app.events.asset_processing import EventValidationError, parse_asset_processing_requested_event
from app.processing.adapters.celery_dispatcher import (
    CeleryProcessingTaskDispatcher,
    encode_processing_task_payload,
)
from app.processing.application.dispatch import DispatchProcessingApplicationService
from app.processing.application.execute import ExecuteProcessingApplicationService
from app.processing.domain.models import (
    ProcessingExecutionCommand,
    ProcessingFailed,
    ProcessingRequestCommand,
    ProcessingSkipped,
    ProcessingSucceeded,
)
from app.processing.ports.request_repository import ProcessingRequestState
from app.processing.ports.task_dispatcher import ProcessingDispatch
from app.tasks.video_tasks import process_asset_object_task, process_video_task


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
    def build_service(self, *, segments=("first", "second"), failure=None, status=None):
        store = MagicMock()
        store.claim.return_value = status
        sink = MagicMock()
        transcriber = MagicMock()
        if failure is None:
            transcriber.transcribe.return_value = segments
        else:
            transcriber.transcribe.side_effect = failure

        class MediaSource:
            @contextmanager
            def acquire(self, _command):
                yield "/tmp/media.mp4"

        fixed_now = datetime(2026, 7, 13, tzinfo=UTC)
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
        transcriber.transcribe.assert_called_once()
        store.persist_success.assert_called_once_with(outcome)
        sink.record.assert_called_once_with(outcome)
        store.commit.assert_called_once_with()

    def test_provider_failure_rolls_back_and_records_one_failed_outcome(self) -> None:
        error = RuntimeError("provider unavailable")
        service, store, sink, _ = self.build_service(failure=error)
        outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingFailed)
        self.assertEqual(outcome.failure.diagnostic_message, "provider unavailable")
        store.rollback.assert_called_once_with()
        store.persist_failure.assert_called_once_with(outcome)
        sink.record.assert_called_once_with(outcome)
        store.commit.assert_called_once_with()

    def test_already_processed_request_skips_media_and_result_recording(self) -> None:
        service, store, sink, transcriber = self.build_service(status="ready")
        outcome = service.execute(command())
        self.assertIsInstance(outcome, ProcessingSkipped)
        transcriber.transcribe.assert_not_called()
        sink.record.assert_not_called()
        store.commit.assert_not_called()


class CeleryWorkerAdapterTest(unittest.TestCase):
    def test_task_names_and_worker_discovery_metadata_are_unchanged(self) -> None:
        self.assertEqual(process_video_task.name, "process_video")
        self.assertEqual(process_asset_object_task.name, "process_asset_object")

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
