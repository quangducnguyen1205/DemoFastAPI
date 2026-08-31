import ast
import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.routing import APIRoute

from app.bootstrap.api import create_api_app
from app.config.settings import settings
from app.bootstrap.consumer import build_processing_dispatch_service
from app.bootstrap.relay import (
    build_result_reconciliation_service,
    build_result_relay_service,
)
from app.consumers import asset_processing_consumer
from app.core.celery_app import celery_app
from app.processing.application.dispatch import DispatchProcessingApplicationService
from app.relays import processing_outbox_relay
from app.result_delivery.application.reconcile import ReconcileFailedProcessingResultsApplicationService
from app.result_delivery.application.relay import RelayProcessingResultsApplicationService


APP_ROOT = Path(__file__).parent / "app"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class StaticArchitectureBoundaryTest(unittest.TestCase):
    def test_processing_application_and_domain_are_framework_neutral(self) -> None:
        forbidden = ("celery", "kafka", "fastapi", "sqlalchemy", "boto3", "whisper")
        for directory in (APP_ROOT / "processing" / "application", APP_ROOT / "processing" / "domain"):
            for path in directory.glob("*.py"):
                with self.subTest(path=path.name):
                    imports = imported_modules(path)
                    self.assertFalse(
                        any(module == prefix or module.startswith(f"{prefix}.") for module in imports for prefix in forbidden),
                        imports,
                    )

    def test_result_delivery_application_does_not_import_persistence_or_kafka_adapters(self) -> None:
        for path in (APP_ROOT / "result_delivery" / "application").glob("*.py"):
            with self.subTest(path=path.name):
                imports = imported_modules(path)
                self.assertFalse(any(module.startswith("app.result_delivery.adapters") for module in imports))
                self.assertFalse(any(module == "kafka" or module.startswith("kafka.") for module in imports))
                self.assertFalse(any(module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in imports))

    def test_transport_adapters_do_not_own_processing_or_result_algorithms(self) -> None:
        consumer_imports = imported_modules(APP_ROOT / "consumers" / "asset_processing_consumer.py")
        task_imports = imported_modules(APP_ROOT / "tasks" / "video_tasks.py")
        self.assertNotIn("app.processing.application.execute", consumer_imports)
        self.assertFalse(any("direct_upload_compatibility" in module for module in consumer_imports))
        self.assertFalse(any("whisper" in module or "object_storage" in module for module in consumer_imports))
        self.assertFalse(any("sqlalchemy" in module for module in task_imports))
        self.assertFalse(any("processing_outbox" in module for module in task_imports))

    def test_relay_and_assistant_features_stay_isolated_from_processing_providers(self) -> None:
        for directory in (APP_ROOT / "result_delivery", APP_ROOT / "services"):
            for path in directory.rglob("*.py"):
                if path.name != "assistant_ollama.py" and directory.name == "services":
                    continue
                imports = imported_modules(path)
                if "result_delivery" in path.parts:
                    self.assertFalse(any("whisper_transcriber" in module for module in imports))
        assistant_paths = [
            APP_ROOT / "routers" / "internal_assistant.py",
            APP_ROOT / "services" / "assistant_ollama.py",
        ]
        for path in assistant_paths:
            imports = imported_modules(path)
            self.assertFalse(any(module.startswith("app.processing") for module in imports))
            self.assertFalse(any(module.startswith("app.result_delivery") for module in imports))


class RuntimeCompositionSmokeTest(unittest.TestCase):
    def test_api_factory_preserves_routes_and_openapi_deprecation_without_startup_io(self) -> None:
        app = create_api_app()
        routes = {
            (route.path, method)
            for route in app.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }
        self.assertTrue(
            {
                ("/", "GET"),
                ("/health", "GET"),
                ("/videos/upload", "POST"),
                ("/videos/tasks/{task_id}", "GET"),
                ("/videos/{video_id}", "GET"),
                ("/videos/{video_id}/transcript", "GET"),
                ("/internal/processing-requests/{processingRequestId}/transcript-rows", "GET"),
                ("/internal/assistant/answer", "POST"),
            }.issubset(routes)
        )
        schema = app.openapi()
        self.assertTrue(schema["paths"]["/videos/upload"]["post"]["deprecated"])
        self.assertEqual(app.title, "AI Knowledge Workspace Processing Service")

    def test_worker_discovery_and_celery_serialization_metadata_are_unchanged(self) -> None:
        self.assertIn("app.tasks.video_tasks", celery_app.conf.include)
        self.assertEqual(celery_app.conf.task_serializer, "json")
        self.assertEqual(celery_app.conf.result_serializer, "json")
        self.assertEqual(celery_app.conf.accept_content, ["json"])
        self.assertIn("process_video", celery_app.tasks)
        self.assertIn("process_asset_object", celery_app.tasks)
        self.assertIn("process_youtube_asset", celery_app.tasks)

    def test_consumer_and_relay_factories_compose_application_services(self) -> None:
        dispatch = build_processing_dispatch_service(MagicMock(), dispatcher=MagicMock())
        self.assertIsInstance(dispatch, DispatchProcessingApplicationService)
        db = MagicMock()
        publisher = MagicMock()
        self.assertIsInstance(build_result_relay_service(db, publisher), RelayProcessingResultsApplicationService)
        self.assertIsInstance(
            build_result_reconciliation_service(db),
            ReconcileFailedProcessingResultsApplicationService,
        )

    def test_consumer_module_entrypoint_delegates_to_consumer_bootstrap(self) -> None:
        with patch("app.bootstrap.consumer.run_processing_consumer") as run:
            asset_processing_consumer.main()
        run.assert_called_once_with()

    def test_bootstrap_modules_import_without_network_or_database_calls(self) -> None:
        for module in (
            "app.bootstrap.api",
            "app.bootstrap.assistant",
            "app.bootstrap.consumer",
            "app.bootstrap.relay",
            "app.bootstrap.worker",
            "app.main",
            "app.tasks.video_tasks",
            "app.relays.processing_outbox_auto_relay",
            "app.relays.processing_outbox_relay",
        ):
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.import_module(module))

    def test_manual_relay_entrypoint_uses_the_shared_composition_service(self) -> None:
        publisher = MagicMock()
        db = MagicMock()
        result = SimpleNamespace(disabled=False, retried=0, failed=0, to_dict=lambda: {"published": 1})
        relay_service = MagicMock()
        relay_service.relay_once.return_value = result
        with (
            patch.object(processing_outbox_relay, "initialize_database_schema"),
            patch.object(processing_outbox_relay, "build_result_publisher", return_value=publisher),
            patch.object(processing_outbox_relay, "SessionLocal", return_value=db),
            patch.object(
                processing_outbox_relay,
                "build_result_relay_service",
                return_value=relay_service,
            ) as builder,
            patch("builtins.print"),
        ):
            exit_code = processing_outbox_relay.main()
        self.assertEqual(exit_code, 0)
        builder.assert_called_once_with(db, publisher)
        relay_service.relay_once.assert_called_once()
        publisher.close.assert_called_once_with()
        db.close.assert_called_once_with()


class KafkaConsumerCommitSemanticsTest(unittest.TestCase):
    def _run_once(self, handler):
        runner = asset_processing_consumer.AssetProcessingKafkaConsumer()
        message = SimpleNamespace(value=b"{}")
        consumer = MagicMock()
        consumer.__iter__.return_value = iter((message,))
        db = MagicMock()

        def stopping_handler(*args):
            runner.stop()
            return handler(*args)

        with (
            patch.object(runner, "build_consumer", return_value=consumer),
            patch.object(asset_processing_consumer, "SessionLocal", return_value=db),
            patch.object(
                asset_processing_consumer,
                "handle_asset_processing_message",
                side_effect=stopping_handler,
            ),
        ):
            runner.run_forever()
        return consumer, db

    def test_valid_or_rejected_message_commits_only_after_handler_returns(self) -> None:
        result = asset_processing_consumer.MessageHandlingResult(
            accepted=False,
            duplicate=False,
            rejected=True,
            reason="unsupported",
        )
        consumer, db = self._run_once(lambda *_args: result)
        consumer.commit.assert_called_once_with()
        consumer.close.assert_called_once_with()
        db.close.assert_called_once_with()

    def test_handoff_failure_leaves_the_offset_uncommitted(self) -> None:
        def fail(*_args):
            raise RuntimeError("handoff failed")

        consumer, db = self._run_once(fail)
        consumer.commit.assert_not_called()
        consumer.close.assert_called_once_with()
        db.close.assert_called_once_with()


def _record(offset: int, value: bytes, *, topic: str = "asset.processing.requested.v1"):
    return SimpleNamespace(topic=topic, partition=0, offset=offset, value=value)


def _accepted(*, duplicate: bool = False) -> asset_processing_consumer.MessageHandlingResult:
    return asset_processing_consumer.MessageHandlingResult(
        accepted=True,
        duplicate=duplicate,
        rejected=False,
        event_id="event-1",
        celery_task_id="asset-processing-event-1",
    )


def _rejected(reason: str) -> asset_processing_consumer.MessageHandlingResult:
    return asset_processing_consumer.MessageHandlingResult(
        accepted=False,
        duplicate=False,
        rejected=True,
        reason=reason,
    )


class _FakeKafkaBroker:
    def __init__(self, records) -> None:
        self.records = list(records)
        self.committed: dict[tuple[str, int], int] = {}


class _FakeKafkaConsumer:
    """Deterministic model of the kafka-python surface run_forever depends on.

    Iteration yields records while advancing the local position to offset + 1, as
    KafkaConsumer._message_generator_v2 does; commit() with no arguments commits every
    tracked position, as SubscriptionState.all_consumed_offsets does; close() never
    commits because the application configures enable_auto_commit=False; and a fresh
    instance resumes from the broker's committed offsets, as a rejoining group member
    does. Unlike the real client, iteration ends once the queued records are exhausted
    so tests terminate instead of blocking.
    """

    def __init__(self, broker: _FakeKafkaBroker, *, commit_error: Exception | None = None) -> None:
        self._broker = broker
        self._positions: dict[tuple[str, int], int] = {}
        self._commit_error = commit_error
        self.commits: list[dict[tuple[str, int], int]] = []
        self.closed = False

    def __iter__(self):
        for record in self._broker.records:
            partition = (record.topic, record.partition)
            if record.offset < self._broker.committed.get(partition, 0):
                continue
            self._positions[partition] = record.offset + 1
            yield record

    def commit(self) -> None:
        if self._commit_error is not None:
            error, self._commit_error = self._commit_error, None
            raise error
        self.commits.append(dict(self._positions))
        self._broker.committed.update(self._positions)

    def close(self) -> None:
        self.closed = True


class KafkaOffsetProgressionTest(unittest.TestCase):
    PARTITION = ("asset.processing.requested.v1", 0)

    def _run(self, runner, consumers, handler, sleeper):
        with (
            patch.object(runner, "build_consumer", side_effect=list(consumers)),
            patch.object(asset_processing_consumer, "SessionLocal", return_value=MagicMock()),
            patch.object(
                asset_processing_consumer,
                "handle_asset_processing_message",
                side_effect=handler,
            ),
            patch.object(asset_processing_consumer.time, "sleep", side_effect=sleeper),
        ):
            runner.run_forever()

    def test_failed_handoff_stops_consumption_before_a_later_commit_can_skip_it(self) -> None:
        broker = _FakeKafkaBroker([_record(100, b"offset-100"), _record(101, b"offset-101")])
        consumer = _FakeKafkaConsumer(broker)
        runner = asset_processing_consumer.AssetProcessingKafkaConsumer()
        handed_off: list[bytes] = []
        sleeps: list[int] = []

        def handler(value, _db, _topic):
            handed_off.append(value)
            if value == b"offset-100":
                raise RuntimeError("transient handoff failure")
            return _accepted()

        def sleep_and_stop(seconds):
            sleeps.append(seconds)
            runner.stop()

        self._run(runner, [consumer], handler, sleep_and_stop)

        self.assertEqual(handed_off, [b"offset-100"])
        self.assertEqual(consumer.commits, [])
        self.assertEqual(broker.committed, {})
        self.assertTrue(consumer.closed)
        self.assertEqual(sleeps, [settings.KAFKA_RECONNECT_BACKOFF_SECONDS])

    def test_uncommitted_record_is_redelivered_and_committed_on_the_next_run(self) -> None:
        broker = _FakeKafkaBroker([_record(100, b"offset-100")])
        first_run = _FakeKafkaConsumer(broker)
        second_run = _FakeKafkaConsumer(broker)
        runner = asset_processing_consumer.AssetProcessingKafkaConsumer()
        attempts: list[bytes] = []
        sleeps: list[int] = []

        def handler(value, _db, _topic):
            attempts.append(value)
            if len(attempts) == 1:
                raise RuntimeError("transient handoff failure")
            runner.stop()
            return _accepted()

        def sleep_with_runaway_guard(seconds):
            sleeps.append(seconds)
            if len(sleeps) > 1:
                runner.stop()

        self._run(runner, [first_run, second_run], handler, sleep_with_runaway_guard)

        self.assertEqual(attempts, [b"offset-100", b"offset-100"])
        self.assertEqual(first_run.commits, [])
        self.assertEqual(second_run.commits, [{self.PARTITION: 101}])
        self.assertEqual(broker.committed, {self.PARTITION: 101})
        self.assertTrue(first_run.closed)
        self.assertTrue(second_run.closed)
        self.assertEqual(sleeps, [settings.KAFKA_RECONNECT_BACKOFF_SECONDS])

    def test_successful_handoffs_commit_every_consumed_position(self) -> None:
        broker = _FakeKafkaBroker([_record(100, b"offset-100"), _record(101, b"offset-101")])
        consumer = _FakeKafkaConsumer(broker)
        runner = asset_processing_consumer.AssetProcessingKafkaConsumer()
        handed_off: list[bytes] = []
        sleeps: list[int] = []

        def handler(value, _db, _topic):
            handed_off.append(value)
            if value == b"offset-101":
                runner.stop()
            return _accepted()

        self._run(runner, [consumer], handler, sleeps.append)

        self.assertEqual(handed_off, [b"offset-100", b"offset-101"])
        self.assertEqual(consumer.commits, [{self.PARTITION: 101}, {self.PARTITION: 102}])
        self.assertEqual(broker.committed, {self.PARTITION: 102})
        self.assertEqual(sleeps, [])
        self.assertTrue(consumer.closed)

    def test_rejected_message_is_committed_and_does_not_restart_consumption(self) -> None:
        broker = _FakeKafkaBroker([_record(100, b"malformed-100"), _record(101, b"offset-101")])
        consumer = _FakeKafkaConsumer(broker)
        runner = asset_processing_consumer.AssetProcessingKafkaConsumer()
        handed_off: list[bytes] = []
        sleeps: list[int] = []

        def handler(value, _db, _topic):
            handed_off.append(value)
            if value == b"malformed-100":
                return _rejected("event validation failed")
            runner.stop()
            return _accepted()

        self._run(runner, [consumer], handler, sleeps.append)

        self.assertEqual(handed_off, [b"malformed-100", b"offset-101"])
        self.assertEqual(consumer.commits, [{self.PARTITION: 101}, {self.PARTITION: 102}])
        self.assertEqual(broker.committed, {self.PARTITION: 102})
        self.assertEqual(sleeps, [])

    def test_commit_failure_restarts_consumption_and_redelivers_instead_of_skipping(self) -> None:
        broker = _FakeKafkaBroker([_record(100, b"offset-100"), _record(101, b"offset-101")])
        first_run = _FakeKafkaConsumer(broker, commit_error=RuntimeError("offset commit failed"))
        second_run = _FakeKafkaConsumer(broker)
        runner = asset_processing_consumer.AssetProcessingKafkaConsumer()
        attempts: list[bytes] = []
        sleeps: list[int] = []

        def handler(value, _db, _topic):
            attempts.append(value)
            if value == b"offset-101":
                runner.stop()
                return _accepted()
            return _accepted(duplicate=len(attempts) > 1)

        def sleep_with_runaway_guard(seconds):
            sleeps.append(seconds)
            if len(sleeps) > 1:
                runner.stop()

        self._run(runner, [first_run, second_run], handler, sleep_with_runaway_guard)

        self.assertEqual(attempts, [b"offset-100", b"offset-100", b"offset-101"])
        self.assertEqual(first_run.commits, [])
        self.assertEqual(second_run.commits, [{self.PARTITION: 101}, {self.PARTITION: 102}])
        self.assertEqual(broker.committed, {self.PARTITION: 102})
        self.assertEqual(sleeps, [settings.KAFKA_RECONNECT_BACKOFF_SECONDS])
        self.assertTrue(first_run.closed)
        self.assertTrue(second_run.closed)


if __name__ == "__main__":
    unittest.main()
