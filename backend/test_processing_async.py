import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app.config.settings as settings_module
from app.events.asset_processing import EventValidationError, parse_asset_processing_requested_event
from app.relays import processing_outbox_auto_relay
from app.services import processing_outbox_publisher, processing_requests
from app.services.processing_outbox import add_transcript_ready_event


def valid_event_dict() -> dict:
    return {
        "eventId": "evt-00000000-0000-4000-8000-000000000001",
        "eventType": "asset.processing.requested",
        "eventVersion": 1,
        "aggregateType": "ASSET",
        "aggregateId": "asset-00000000-0000-4000-8000-000000000001",
        "occurredAt": "2026-07-11T00:00:00Z",
        "payload": {
            "assetId": "asset-00000000-0000-4000-8000-000000000001",
            "workspaceId": "workspace-00000000-0000-4000-8000-000000000001",
            "ownerId": "owner-example",
            "storageBucket": "workspace-media",
            "objectKey": "objects/synthetic-video.mp4",
            "originalFilename": "synthetic-video.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 128,
            "requestedAt": "2026-07-11T00:00:00Z",
        },
    }


class AssetProcessingEventValidationTest(unittest.TestCase):
    def test_valid_event_preserves_the_object_reference_contract(self) -> None:
        event = parse_asset_processing_requested_event(valid_event_dict())
        self.assertEqual(event.eventVersion, 1)
        self.assertEqual(event.payload.storageBucket, "workspace-media")
        self.assertEqual(event.payload.objectKey, "objects/synthetic-video.mp4")

    def test_unsupported_event_version_is_rejected(self) -> None:
        raw_event = valid_event_dict()
        raw_event["eventVersion"] = 2
        with self.assertRaises(EventValidationError):
            parse_asset_processing_requested_event(raw_event)


class ProcessingRequestAcceptanceTest(unittest.TestCase):
    def test_completed_duplicate_does_not_enqueue_another_celery_task(self) -> None:
        event = parse_asset_processing_requested_event(valid_event_dict())
        existing = SimpleNamespace(
            event_id=event.eventId,
            asset_id=event.payload.assetId,
            status="ready",
            celery_task_id="asset-processing-existing",
        )
        enqueue = MagicMock()
        with patch.object(processing_requests, "_get_or_create_processing_request", return_value=existing):
            result = processing_requests.accept_processing_event(MagicMock(), event, enqueue=enqueue)
        self.assertTrue(result.accepted)
        self.assertTrue(result.duplicate)
        self.assertEqual(result.celery_task_id, "asset-processing-existing")
        enqueue.assert_not_called()

    def test_new_request_enqueues_once_with_a_deterministic_task_id(self) -> None:
        event = parse_asset_processing_requested_event(valid_event_dict())
        accepted = SimpleNamespace(
            event_id=event.eventId,
            asset_id=event.payload.assetId,
            status="accepted",
            celery_task_id=None,
        )
        enqueued = SimpleNamespace(
            event_id=event.eventId,
            asset_id=event.payload.assetId,
            status="enqueued",
            celery_task_id=f"asset-processing-{event.eventId}",
            storage_bucket=event.payload.storageBucket,
            object_key=event.payload.objectKey,
        )
        query = MagicMock()
        query.filter.return_value = query
        query.update.return_value = 1
        query.one.return_value = enqueued
        db = MagicMock()
        db.query.return_value = query
        enqueue = MagicMock(return_value=SimpleNamespace(id=enqueued.celery_task_id))

        with patch.object(processing_requests, "_get_or_create_processing_request", return_value=accepted):
            result = processing_requests.accept_processing_event(db, event, enqueue=enqueue)

        enqueue.assert_called_once_with(
            args=[event.to_celery_payload()],
            task_id=f"asset-processing-{event.eventId}",
        )
        db.commit.assert_called_once()
        self.assertFalse(result.duplicate)
        self.assertEqual(result.status, "enqueued")


class ProcessingResultOutboxTest(unittest.TestCase):
    def test_terminal_ready_result_creates_one_outbox_intent(self) -> None:
        query = MagicMock()
        query.filter.return_value = query
        query.first.return_value = None
        db = MagicMock()
        db.query.return_value = query
        request = SimpleNamespace(
            event_id="evt-00000000-0000-4000-8000-000000000001",
            asset_id="asset-00000000-0000-4000-8000-000000000001",
        )

        event = add_transcript_ready_event(db, processing_request=request, segment_count=3)

        db.add.assert_called_once()
        persisted = db.add.call_args.args[0]
        self.assertEqual(persisted.id, event.id)
        self.assertEqual(persisted.payload, event.payload)
        self.assertEqual(event.event_type, "transcript.ready")
        self.assertEqual(event.causation_event_id, request.event_id)
        self.assertEqual(event.payload["status"], "ready")
        self.assertEqual(event.payload["segmentCount"], 3)


class AutomaticRelayConfigurationTest(unittest.TestCase):
    def test_auto_relay_requires_both_enablement_and_publisher_gates(self) -> None:
        combinations = ((False, False, False), (True, False, False), (False, True, False), (True, True, True))
        for auto_enabled, publisher_enabled, expected in combinations:
            with self.subTest(auto_enabled=auto_enabled, publisher_enabled=publisher_enabled):
                with (
                    patch.object(
                        processing_outbox_auto_relay.settings,
                        "PROCESSING_OUTBOX_AUTO_RELAY_ENABLED",
                        auto_enabled,
                    ),
                    patch.object(
                        processing_outbox_auto_relay.settings,
                        "PROCESSING_RESULT_PUBLISHER_ENABLED",
                        publisher_enabled,
                    ),
                ):
                    self.assertIs(processing_outbox_auto_relay._auto_relay_configuration_is_valid(), expected)

    def test_publisher_factory_fails_closed_unless_explicitly_enabled(self) -> None:
        with patch.object(processing_outbox_publisher.settings, "PROCESSING_RESULT_PUBLISHER_ENABLED", False):
            publisher = processing_outbox_publisher.build_processing_outbox_publisher()
            self.assertIsInstance(publisher, processing_outbox_publisher.DisabledProcessingOutboxPublisher)

        with patch.object(processing_outbox_publisher.settings, "PROCESSING_RESULT_PUBLISHER_ENABLED", True):
            publisher = processing_outbox_publisher.build_processing_outbox_publisher()
            self.assertIsInstance(publisher, processing_outbox_publisher.KafkaProcessingOutboxPublisher)


class ProcessingSettingsTest(unittest.TestCase):
    def test_generic_defaults_keep_result_relays_disabled(self) -> None:
        settings = self._load_settings({})
        self.assertFalse(settings.PROCESSING_RESULT_PUBLISHER_ENABLED)
        self.assertFalse(settings.PROCESSING_OUTBOX_RELAY_ENABLED)
        self.assertFalse(settings.PROCESSING_OUTBOX_AUTO_RELAY_ENABLED)
        self.assertFalse(settings.PROCESSING_OUTBOX_RECOVERY_ENABLED)
        self.assertEqual(settings.PROCESSING_OUTBOX_RECOVERY_INTERVAL_SECONDS, 30)
        self.assertEqual(settings.PROCESSING_OUTBOX_RECOVERY_COOLDOWN_SECONDS, 60)
        self.assertEqual(settings.PROCESSING_OUTBOX_RECOVERY_BATCH_SIZE, 50)
        self.assertEqual(settings.PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES, 3)

    def test_integrated_overrides_enable_relay_and_validated_assistant_values(self) -> None:
        settings = self._load_settings(
            {
                "PROCESSING_RESULT_PUBLISHER_ENABLED": "true",
                "PROCESSING_OUTBOX_AUTO_RELAY_ENABLED": "true",
                "PROCESSING_OUTBOX_RECOVERY_ENABLED": "true",
                "PROCESSING_OUTBOX_RECOVERY_INTERVAL_SECONDS": "30",
                "PROCESSING_OUTBOX_RECOVERY_COOLDOWN_SECONDS": "60",
                "PROCESSING_OUTBOX_RECOVERY_BATCH_SIZE": "50",
                "PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES": "3",
                "ASSISTANT_LLM_ENABLED": "true",
                "ASSISTANT_OLLAMA_MODEL": "qwen3:4b",
                "ASSISTANT_OLLAMA_TIMEOUT_SECONDS": "60",
                "ASSISTANT_OLLAMA_NUM_PREDICT": "256",
            }
        )
        self.assertTrue(settings.PROCESSING_RESULT_PUBLISHER_ENABLED)
        self.assertTrue(settings.PROCESSING_OUTBOX_AUTO_RELAY_ENABLED)
        self.assertTrue(settings.PROCESSING_OUTBOX_RECOVERY_ENABLED)
        self.assertTrue(settings.ASSISTANT_LLM_ENABLED)
        self.assertEqual(settings.ASSISTANT_OLLAMA_MODEL, "qwen3:4b")
        self.assertEqual(settings.ASSISTANT_OLLAMA_TIMEOUT_SECONDS, 60.0)
        self.assertEqual(settings.ASSISTANT_OLLAMA_NUM_PREDICT, 256)

    def test_invalid_recovery_bounds_fail_configuration(self) -> None:
        for overrides in (
            {"PROCESSING_OUTBOX_RECOVERY_INTERVAL_SECONDS": "0"},
            {"PROCESSING_OUTBOX_RECOVERY_COOLDOWN_SECONDS": "0"},
            {"PROCESSING_OUTBOX_RECOVERY_BATCH_SIZE": "1001"},
            {"PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES": "101"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self._load_settings(overrides)

    def _load_settings(self, overrides: dict[str, str]):
        environment = {"DOTENV_PATH": "/tmp/nonexistent-project3-env", **overrides}
        try:
            with patch.dict(os.environ, environment, clear=True):
                return importlib.reload(settings_module).settings
        finally:
            importlib.reload(settings_module)


if __name__ == "__main__":
    unittest.main()
