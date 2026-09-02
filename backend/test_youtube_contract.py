import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import models
from app.config.settings import settings
from app.consumers.asset_processing_consumer import (
    AssetProcessingKafkaConsumer,
    handle_asset_processing_message,
)
from app.core import schema
from app.core.database import Base
from app.events.asset_processing import (
    EventValidationError,
    parse_asset_processing_requested_event,
)
from app.events.asset_processing_v2 import (
    parse_youtube_asset_processing_requested_event,
)
from app.processing.adapters.celery_dispatcher import (
    CeleryProcessingTaskDispatcher,
    CeleryYouTubeProcessingTaskDispatcher,
    SourceAwareCeleryProcessingTaskDispatcher,
    decode_youtube_processing_task_payload,
)
from app.processing.adapters.sqlalchemy_stores import (
    SqlAlchemyProcessingRequestRepository,
)
from app.processing.application.dispatch import DispatchProcessingApplicationService
from app.processing.domain.models import (
    ConflictingProcessingRequestError,
    ProcessingExecutionCommand,
    ProcessingRequestCommand,
    YouTubeProcessingExecutionCommand,
    YouTubeProcessingRequestCommand,
)
from app.tasks.video_tasks import (
    process_asset_object_task,
    process_youtube_asset_task,
)


def v1_event() -> dict:
    return {
        "eventId": "v1-event",
        "eventType": "asset.processing.requested",
        "eventVersion": 1,
        "aggregateType": "ASSET",
        "aggregateId": "asset-v1",
        "occurredAt": "2026-07-27T00:00:00Z",
        "payload": {
            "assetId": "asset-v1",
            "workspaceId": "workspace-1",
            "ownerId": "owner-1",
            "storageBucket": "workspace-media",
            "objectKey": "objects/media.mp4",
            "originalFilename": "media.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 128,
            "requestedAt": "2026-07-27T00:00:00Z",
        },
    }


def v2_event(
    *,
    event_id: str = "v2-event",
    asset_id: str = "asset-v2",
    youtube_video_id: str = "abc_DEF-123",
) -> dict:
    return {
        "eventId": event_id,
        "eventType": "asset.processing.requested",
        "eventVersion": 2,
        "aggregateType": "ASSET",
        "aggregateId": asset_id,
        "occurredAt": "2026-07-27T00:00:00Z",
        "payload": {
            "assetId": asset_id,
            "workspaceId": "workspace-1",
            "ownerId": "owner-1",
            "sourceType": "YOUTUBE",
            "youtubeVideoId": youtube_video_id,
            "requestedAt": "2026-07-27T00:00:00Z",
        },
    }


CONTRACT_FIXTURES = Path(__file__).parent / "tests" / "contract"


def canonical_payload(name: str) -> dict:
    with open(CONTRACT_FIXTURES / name, encoding="utf-8") as handle:
        return json.loads(handle.read())


class ProducerSerializationContractTest(unittest.TestCase):
    """Parses the exact bytes the producer's own serializer emits.

    The fixtures under tests/contract mirror
    services/workspace-core/src/test/resources/contract in ai-knowledge-workspace, where they are
    asserted byte for byte against the application-managed ObjectMapper. Hand-written payloads in
    this file prove only that the parser accepts what this repository imagines; these prove it
    accepts what Spring actually publishes. requestedAt is the field that matters: the payload
    models below declare it as a string, and a mapper configured to write timestamps would emit a
    number here instead.
    """

    def test_v1_payload_from_the_producer_serializer_is_accepted(self) -> None:
        payload = canonical_payload("processing-requested-v1.json")
        self.assertIsInstance(payload["requestedAt"], str)

        event = parse_asset_processing_requested_event(
            {
                "eventId": "44444444-4444-4444-4444-444444444444",
                "eventType": "asset.processing.requested",
                "eventVersion": 1,
                "aggregateType": "Asset",
                "aggregateId": payload["assetId"],
                "occurredAt": "2026-07-01T10:15:30Z",
                "payload": payload,
            }
        )
        command = event.to_processing_command()
        self.assertIsInstance(command, ProcessingRequestCommand)
        self.assertEqual(command.asset_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(command.storage_bucket, "workspace-media")
        self.assertEqual(
            command.object_key,
            "users/learner-1/workspaces/learning/assets/lesson/raw/lesson.mp4",
        )
        self.assertEqual(command.content_type, "video/mp4")
        self.assertEqual(command.size_bytes, 4096)
        self.assertEqual(command.requested_at, "2026-07-01T10:15:30Z")

    def test_v2_payload_from_the_producer_serializer_is_accepted(self) -> None:
        payload = canonical_payload("processing-requested-v2.json")
        self.assertIsInstance(payload["requestedAt"], str)

        event = parse_youtube_asset_processing_requested_event(
            {
                "eventId": "44444444-4444-4444-4444-444444444444",
                "eventType": "asset.processing.requested",
                "eventVersion": 2,
                "aggregateType": "ASSET",
                "aggregateId": payload["assetId"],
                "occurredAt": "2026-07-01T10:15:30Z",
                "payload": payload,
            }
        )
        command = event.to_processing_command()
        self.assertIsInstance(command, YouTubeProcessingRequestCommand)
        self.assertEqual(command.youtube_video_id, "abc_DEF-123")
        self.assertEqual(command.requested_at, "2026-07-01T10:15:30Z")

    def test_a_timestamp_number_for_requested_at_is_rejected_not_silently_coerced(self) -> None:
        # Guards the direction of the drift: if the producer were ever reconfigured to write dates
        # as timestamps, this consumer would reject the event rather than accept a wrong value.
        payload = canonical_payload("processing-requested-v1.json")
        payload["requestedAt"] = 1783246530
        with self.assertRaises(EventValidationError):
            parse_asset_processing_requested_event(
                {
                    "eventId": "44444444-4444-4444-4444-444444444444",
                    "eventType": "asset.processing.requested",
                    "eventVersion": 1,
                    "aggregateType": "Asset",
                    "aggregateId": payload["assetId"],
                    "occurredAt": "2026-07-01T10:15:30Z",
                    "payload": payload,
                }
            )


class YouTubeV2EventContractTest(unittest.TestCase):
    def test_exact_v1_contract_and_task_remain_unchanged(self) -> None:
        event = parse_asset_processing_requested_event(v1_event())
        command = event.to_processing_command()
        self.assertIsInstance(command, ProcessingRequestCommand)
        self.assertEqual(settings.KAFKA_ASSET_PROCESSING_TOPIC, "asset.processing.requested.v1")
        self.assertEqual(process_asset_object_task.name, "process_asset_object")
        self.assertEqual(command.storage_bucket, "workspace-media")
        self.assertEqual(command.object_key, "objects/media.mp4")

    def test_valid_exact_v2_event_is_accepted(self) -> None:
        event = parse_youtube_asset_processing_requested_event(v2_event())
        command = event.to_processing_command()
        self.assertIsInstance(command, YouTubeProcessingRequestCommand)
        self.assertEqual(event.eventType, "asset.processing.requested")
        self.assertEqual(event.eventVersion, 2)
        self.assertEqual(command.youtube_video_id, "abc_DEF-123")
        self.assertEqual(settings.KAFKA_ASSET_PROCESSING_V2_TOPIC, "asset.processing.requested.v2")

    def test_invalid_version_source_type_and_extra_url_are_rejected(self) -> None:
        invalid_cases = []
        wrong_version = v2_event()
        wrong_version["eventVersion"] = 1
        invalid_cases.append(wrong_version)
        wrong_source = v2_event()
        wrong_source["payload"]["sourceType"] = "OBJECT_STORAGE"
        invalid_cases.append(wrong_source)
        arbitrary_url = v2_event()
        arbitrary_url["payload"]["url"] = "https://example.invalid/video"
        invalid_cases.append(arbitrary_url)
        conflicting_source = v2_event()
        conflicting_source["payload"]["storageBucket"] = "fake"
        invalid_cases.append(conflicting_source)

        for raw_event in invalid_cases:
            with self.subTest(raw_event=raw_event), self.assertRaises(EventValidationError):
                parse_youtube_asset_processing_requested_event(raw_event)

    def test_malformed_or_unsafe_video_ids_are_rejected(self) -> None:
        for video_id in ("", "   ", "bad/id", "bad.id", "../escape", "x" * 65):
            with self.subTest(video_id=video_id), self.assertRaises(EventValidationError):
                parse_youtube_asset_processing_requested_event(
                    v2_event(youtube_video_id=video_id)
                )


class YouTubeDispatchAndIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_v2_dispatch_uses_only_validated_id_and_explicit_task(self) -> None:
        enqueue = MagicMock(return_value=SimpleNamespace(id="youtube-processing-v2-event"))
        dispatcher = CeleryYouTubeProcessingTaskDispatcher(enqueue)
        command = (
            parse_youtube_asset_processing_requested_event(v2_event())
            .to_processing_command()
            .to_execution_command()
        )
        dispatched = dispatcher.dispatch(command)
        enqueue.assert_called_once_with(
            args=[
                {
                    "eventId": "v2-event",
                    "assetId": "asset-v2",
                    "workspaceId": "workspace-1",
                    "ownerId": "owner-1",
                    "youtubeVideoId": "abc_DEF-123",
                }
            ],
            task_id="youtube-processing-v2-event",
        )
        self.assertEqual(dispatched.task_id, "youtube-processing-v2-event")
        self.assertEqual(process_youtube_asset_task.name, "process_youtube_asset")

    def test_youtube_task_boundary_revalidates_video_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid YouTube"):
            decode_youtube_processing_task_payload({
                "eventId": "v2-event",
                "assetId": "asset-v2",
                "workspaceId": "workspace-1",
                "ownerId": "owner-1",
                "youtubeVideoId": "https://example.invalid/video",
            })

    def test_source_aware_dispatch_does_not_change_v1_routing(self) -> None:
        object_dispatcher = MagicMock(spec=CeleryProcessingTaskDispatcher)
        youtube_dispatcher = MagicMock(spec=CeleryYouTubeProcessingTaskDispatcher)
        router = SourceAwareCeleryProcessingTaskDispatcher(
            object_storage_dispatcher=object_dispatcher,
            youtube_dispatcher=youtube_dispatcher,
        )
        v1_command = (
            parse_asset_processing_requested_event(v1_event())
            .to_processing_command()
            .to_execution_command()
        )
        v2_command = (
            parse_youtube_asset_processing_requested_event(v2_event())
            .to_processing_command()
            .to_execution_command()
        )
        router.dispatch(v1_command)
        router.dispatch(v2_command)
        object_dispatcher.dispatch.assert_called_once_with(v1_command)
        youtube_dispatcher.dispatch.assert_called_once_with(v2_command)

    def test_duplicate_v2_is_idempotent_and_conflicting_reuse_is_rejected(self) -> None:
        db = self.Session()
        repository = SqlAlchemyProcessingRequestRepository(db)
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = SimpleNamespace(task_id="youtube-processing-v2-event")
        service = DispatchProcessingApplicationService(
            repository=repository,
            dispatcher=dispatcher,
        )
        command = parse_youtube_asset_processing_requested_event(
            v2_event()
        ).to_processing_command()
        first = service.dispatch(command)
        second = service.dispatch(command)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        dispatcher.dispatch.assert_called_once()
        self.assertEqual(db.query(models.ProcessingRequest).count(), 1)

        conflicting = parse_youtube_asset_processing_requested_event(
            v2_event(asset_id="different-asset")
        ).to_processing_command()
        with self.assertRaises(ConflictingProcessingRequestError):
            service.dispatch(conflicting)
        self.assertEqual(db.query(models.ProcessingRequest).count(), 1)
        db.close()

    def test_v1_request_persists_as_object_storage_without_changing_payload(self) -> None:
        db = self.Session()
        repository = SqlAlchemyProcessingRequestRepository(db)
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = SimpleNamespace(
            task_id="asset-processing-v1-event"
        )
        command = parse_asset_processing_requested_event(
            v1_event()
        ).to_processing_command()
        DispatchProcessingApplicationService(
            repository=repository,
            dispatcher=dispatcher,
        ).dispatch(command)
        saved = db.query(models.ProcessingRequest).one()
        self.assertEqual(saved.source_type, "OBJECT_STORAGE")
        self.assertIsNone(saved.youtube_video_id)
        self.assertEqual(saved.storage_bucket, "workspace-media")
        self.assertEqual(saved.object_key, "objects/media.mp4")
        dispatcher.dispatch.assert_called_once_with(command.to_execution_command())
        db.close()


class ProcessingRequestSourceSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_fresh_schema_supports_both_named_source_shapes(self) -> None:
        columns = {
            column["name"]: column
            for column in inspect(self.engine).get_columns("processing_requests")
        }
        constraints = {
            constraint["name"]
            for constraint in inspect(self.engine).get_check_constraints(
                "processing_requests"
            )
        }
        self.assertFalse(columns["source_type"]["nullable"])
        self.assertTrue(columns["youtube_video_id"]["nullable"])
        for name in (
            "storage_bucket",
            "object_key",
            "original_filename",
            "content_type",
            "size_bytes",
        ):
            self.assertTrue(columns[name]["nullable"])
        self.assertIn("ck_processing_request_source_shape", constraints)
        self.assertIn("ck_processing_request_youtube_video_id", constraints)
        self.assertIn("ck_processing_request_lease_shape", constraints)

        db = self.Session()
        db.add(models.ProcessingRequest(
            event_id="object-row",
            asset_id="asset-1",
            source_type="OBJECT_STORAGE",
            storage_bucket="workspace-media",
            object_key="objects/media.mp4",
            content_type="video/mp4",
            size_bytes=128,
            status="accepted",
        ))
        db.add(models.ProcessingRequest(
            event_id="youtube-row",
            asset_id="asset-2",
            source_type="YOUTUBE",
            youtube_video_id="abc_DEF-123",
            status="accepted",
        ))
        db.commit()
        self.assertEqual(db.query(models.ProcessingRequest).count(), 2)
        db.close()

    def test_existing_schema_upgrades_idempotently_and_backfills_object_storage(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with engine.begin() as connection:
                connection.execute(text("""
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
                        processing_started_at TIMESTAMP,
                        lease_expires_at TIMESTAMP,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        occurred_at VARCHAR(64),
                        requested_at VARCHAR(64),
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
                    )
                """))
                connection.execute(text("""
                    INSERT INTO processing_requests (
                        event_id, asset_id, storage_bucket, object_key,
                        content_type, size_bytes, status
                    ) VALUES (
                        'legacy-v1', 'asset-1', 'workspace-media',
                        'objects/media.mp4', 'video/mp4', 128, 'ready'
                    )
                """))

            schema.ensure_processing_request_source_schema(engine)
            schema.ensure_processing_request_source_schema(engine)
            columns = {
                column["name"]: column
                for column in inspect(engine).get_columns("processing_requests")
            }
            self.assertTrue(columns["storage_bucket"]["nullable"])
            Session = sessionmaker(bind=engine)
            db = Session()
            legacy = db.query(models.ProcessingRequest).one()
            self.assertEqual(legacy.source_type, "OBJECT_STORAGE")
            self.assertIsNone(legacy.youtube_video_id)
            db.add(models.ProcessingRequest(
                event_id="youtube-after-upgrade",
                asset_id="asset-2",
                source_type="YOUTUBE",
                youtube_video_id="safe_ID-2",
                status="accepted",
            ))
            db.commit()
            db.close()
        finally:
            engine.dispose()

    def test_mixed_shapes_invalid_video_id_and_lease_shape_are_rejected(self) -> None:
        invalid_rows = (
            models.ProcessingRequest(
                event_id="mixed-object",
                asset_id="asset-1",
                source_type="OBJECT_STORAGE",
                youtube_video_id="unexpected",
                storage_bucket="bucket",
                object_key="key",
                content_type="video/mp4",
                size_bytes=1,
                status="accepted",
            ),
            models.ProcessingRequest(
                event_id="mixed-youtube",
                asset_id="asset-2",
                source_type="YOUTUBE",
                youtube_video_id="safe_ID",
                storage_bucket="unexpected",
                status="accepted",
            ),
            models.ProcessingRequest(
                event_id="unsafe-id",
                asset_id="asset-3",
                source_type="YOUTUBE",
                youtube_video_id="../unsafe",
                status="accepted",
            ),
            models.ProcessingRequest(
                event_id="invalid-lease",
                asset_id="asset-4",
                source_type="YOUTUBE",
                youtube_video_id="safe_ID",
                status="processing",
                processing_started_at=datetime(2026, 7, 27, tzinfo=UTC),
                lease_expires_at=None,
                attempt_count=1,
            ),
        )
        for row in invalid_rows:
            db = self.Session()
            db.add(row)
            with self.subTest(event_id=row.event_id), self.assertRaises(
                (IntegrityError, TypeError)
            ):
                db.commit()
            db.rollback()
            db.close()

    def test_postgresql_constraints_are_scoped_to_current_schema_and_table(self) -> None:
        connection = MagicMock()
        schema._apply_processing_request_source_schema(
            connection,
            "postgresql",
            {
                "source_type": {"nullable": False},
                "youtube_video_id": {"nullable": True},
            },
            set(),
        )
        constraint_sql = next(
            str(call.args[0])
            for call in connection.execute.call_args_list
            if "ck_processing_request_source_shape" in str(call.args[0])
        )
        self.assertIn("table_record.relname = 'processing_requests'", constraint_sql)
        self.assertIn("schema_record.nspname = current_schema()", constraint_sql)
        self.assertIn("ck_processing_request_youtube_video_id", constraint_sql)


class DualTopicConsumerTest(unittest.TestCase):
    def test_consumer_subscribes_to_v1_and_v2_with_existing_group(self) -> None:
        kafka_module = ModuleType("kafka")
        kafka_consumer = MagicMock()
        kafka_module.KafkaConsumer = kafka_consumer
        with patch.dict(sys.modules, {"kafka": kafka_module}):
            AssetProcessingKafkaConsumer().build_consumer()
        positional = kafka_consumer.call_args.args
        self.assertEqual(
            positional,
            (
                settings.KAFKA_ASSET_PROCESSING_TOPIC,
                settings.KAFKA_ASSET_PROCESSING_V2_TOPIC,
            ),
        )
        self.assertEqual(
            kafka_consumer.call_args.kwargs["group_id"],
            settings.KAFKA_CONSUMER_GROUP,
        )
        self.assertFalse(kafka_consumer.call_args.kwargs["enable_auto_commit"])

    def test_topics_route_to_distinct_validated_commands(self) -> None:
        service = MagicMock()
        service.dispatch.return_value = SimpleNamespace(
            accepted=True,
            duplicate=False,
            event_id="event",
            task_id="task",
        )
        with patch(
            "app.consumers.asset_processing_consumer.build_processing_dispatch_service",
            return_value=service,
        ):
            v1_result = handle_asset_processing_message(
                v1_event(),
                MagicMock(),
                settings.KAFKA_ASSET_PROCESSING_TOPIC,
            )
            v1_command = service.dispatch.call_args.args[0]
            v2_result = handle_asset_processing_message(
                v2_event(),
                MagicMock(),
                settings.KAFKA_ASSET_PROCESSING_V2_TOPIC,
            )
            v2_command = service.dispatch.call_args.args[0]
        self.assertTrue(v1_result.accepted)
        self.assertTrue(v2_result.accepted)
        self.assertIsInstance(v1_command, ProcessingRequestCommand)
        self.assertIsInstance(v2_command, YouTubeProcessingRequestCommand)

    def test_topic_version_mismatch_is_rejected_without_dispatch(self) -> None:
        service = MagicMock()
        with patch(
            "app.consumers.asset_processing_consumer.build_processing_dispatch_service",
            return_value=service,
        ):
            result = handle_asset_processing_message(
                v2_event(),
                MagicMock(),
                settings.KAFKA_ASSET_PROCESSING_TOPIC,
            )
        self.assertTrue(result.rejected)
        service.dispatch.assert_not_called()

    def test_invalid_v2_log_does_not_echo_an_arbitrary_url(self) -> None:
        invalid = v2_event()
        invalid["payload"]["url"] = "https://example.invalid/private?token=secret"
        with self.assertLogs(
            "app.consumers.asset_processing_consumer",
            level="WARNING",
        ) as captured:
            result = handle_asset_processing_message(
                invalid,
                MagicMock(),
                settings.KAFKA_ASSET_PROCESSING_V2_TOPIC,
            )
        self.assertTrue(result.rejected)
        rendered = "\n".join(captured.output)
        self.assertNotIn("example.invalid", rendered)
        self.assertNotIn("secret", rendered)
        self.assertIn("eventVersion", rendered)

    def test_valid_v2_offset_commits_after_dispatch_returns(self) -> None:
        runner = AssetProcessingKafkaConsumer()
        message = SimpleNamespace(
            value=v2_event(),
            topic=settings.KAFKA_ASSET_PROCESSING_V2_TOPIC,
        )
        consumer = MagicMock()
        consumer.__iter__.return_value = iter((message,))
        db = MagicMock()
        service = MagicMock()

        def durable_dispatch(command):
            runner.stop()
            self.assertIsInstance(command, YouTubeProcessingRequestCommand)
            return SimpleNamespace(
                accepted=True,
                duplicate=False,
                event_id=command.event_id,
                task_id="youtube-processing-v2-event",
            )

        service.dispatch.side_effect = durable_dispatch
        with (
            patch.object(runner, "build_consumer", return_value=consumer),
            patch(
                "app.consumers.asset_processing_consumer.SessionLocal",
                return_value=db,
            ),
            patch(
                "app.consumers.asset_processing_consumer.build_processing_dispatch_service",
                return_value=service,
            ),
        ):
            runner.run_forever()
        service.dispatch.assert_called_once()
        consumer.commit.assert_called_once_with()
        consumer.close.assert_called_once_with()
        db.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
