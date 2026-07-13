import ast
import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.routing import APIRoute

from app.bootstrap.api import create_api_app
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


if __name__ == "__main__":
    unittest.main()
