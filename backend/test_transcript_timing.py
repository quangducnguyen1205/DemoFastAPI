import math
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import models
from app.core import schema
from app.core.database import Base
from app.processing.adapters.sqlalchemy_stores import SqlAlchemyProcessingArtifactStore
from app.processing.adapters.whisper_transcriber import (
    WhisperProcessingTranscriptionProvider,
    normalize_whisper_result,
    seconds_to_milliseconds,
)
from app.processing.domain.models import (
    ProcessingArtifact,
    ProcessingSucceeded,
    ProcessingTranscriptRow,
)
from app.routers.internal_processing import get_processing_request_transcript_rows


class WhisperTimestampNormalizationTest(unittest.TestCase):
    def test_seconds_are_rounded_to_integer_milliseconds_and_zero_is_preserved(self) -> None:
        self.assertEqual(seconds_to_milliseconds(0), 0)
        self.assertEqual(seconds_to_milliseconds(1.2344), 1234)
        self.assertEqual(seconds_to_milliseconds(1.2346), 1235)

    def test_exact_half_milliseconds_use_python_round_half_to_even(self) -> None:
        self.assertEqual(seconds_to_milliseconds(0.0005), 0)
        self.assertEqual(seconds_to_milliseconds(0.0015), 2)

    def test_missing_timing_remains_absent(self) -> None:
        self.assertIsNone(seconds_to_milliseconds(None))
        rows = normalize_whisper_result({"segments": [{"text": "legacy"}]})
        self.assertEqual(rows, (ProcessingTranscriptRow(0, "legacy", None, None),))

    def test_invalid_provider_timing_is_rejected(self) -> None:
        for value in (-0.001, math.nan, math.inf, "1.0", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                seconds_to_milliseconds(value)
        with self.assertRaisesRegex(ValueError, "both be present"):
            normalize_whisper_result({"segments": [{"text": "partial", "start": 1.0}]})
        with self.assertRaisesRegex(ValueError, "must not precede"):
            normalize_whisper_result({"segments": [{"text": "backward", "start": 2.0, "end": 1.0}]})

    def test_whisper_adapter_preserves_provider_segments_and_normalizes_once(self) -> None:
        result = {
            "text": "first second",
            "segments": [
                {"text": " first ", "start": 0.0, "end": 1.2504},
                {"text": "second", "start": 1.2504, "end": 2.5},
            ],
        }
        with (
            patch(
                "app.processing.adapters.whisper_transcriber.extract_audio_to_wav",
                return_value="/tmp/audio.wav",
            ),
            patch(
                "app.processing.adapters.whisper_transcriber.transcribe_audio_with_whisper",
                return_value=result,
            ),
            patch("app.processing.adapters.whisper_transcriber.segment_text") as fallback_chunker,
        ):
            rows = WhisperProcessingTranscriptionProvider().transcribe("/tmp/media.mp4")

        self.assertEqual(
            rows,
            (
                ProcessingTranscriptRow(0, "first", 0, 1250),
                ProcessingTranscriptRow(1, "second", 1250, 2500),
            ),
        )
        fallback_chunker.assert_not_called()


class TranscriptArtifactCompatibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _request(self, db, *, status: str = "processing") -> models.ProcessingRequest:
        request = models.ProcessingRequest(
            event_id=str(uuid4()),
            asset_id=str(uuid4()),
            storage_bucket="workspace-media",
            object_key="objects/media.mp4",
            content_type="video/mp4",
            size_bytes=128,
            status=status,
        )
        db.add(request)
        db.commit()
        return request

    def test_artifact_persistence_and_internal_json_use_integer_milliseconds(self) -> None:
        db = self.Session()
        request = self._request(db)
        outcome = ProcessingSucceeded(
            request.event_id,
            request.asset_id,
            ProcessingArtifact((ProcessingTranscriptRow(0, "first", 0, 1235),)),
            datetime(2026, 7, 22, tzinfo=UTC),
        )
        store = SqlAlchemyProcessingArtifactStore(db)
        store.persist_success(outcome)
        store.commit()

        saved = db.query(models.ProcessingRequestTranscript).one()
        self.assertEqual((saved.start_ms, saved.end_ms), (0, 1235))
        response = get_processing_request_transcript_rows(request.event_id, db)
        payload = response[0].model_dump(mode="json")
        self.assertEqual(payload["start_ms"], 0)
        self.assertEqual(payload["end_ms"], 1235)
        self.assertNotIn("start_seconds", payload)
        db.close()

    def test_legacy_artifact_without_timing_remains_readable(self) -> None:
        db = self.Session()
        request = self._request(db, status="ready")
        db.add(models.ProcessingRequestTranscript(
            processing_request_event_id=request.event_id,
            segment_index=0,
            text="legacy",
        ))
        db.commit()

        payload = get_processing_request_transcript_rows(request.event_id, db)[0].model_dump(mode="json")
        self.assertIsNone(payload["start_ms"])
        self.assertIsNone(payload["end_ms"])
        db.close()

    def test_database_constraint_rejects_partial_or_backward_timing(self) -> None:
        for start_ms, end_ms in ((0, None), (100, 99), (-1, 0)):
            db = self.Session()
            request = self._request(db)
            db.add(models.ProcessingRequestTranscript(
                processing_request_event_id=request.event_id,
                segment_index=0,
                text="invalid",
                start_ms=start_ms,
                end_ms=end_ms,
            ))
            with self.subTest(start_ms=start_ms, end_ms=end_ms), self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            db.close()

    def test_fresh_schema_contains_timing_columns_and_named_constraint(self) -> None:
        inspector = inspect(self.engine)
        columns = {column["name"] for column in inspector.get_columns("processing_request_transcripts")}
        checks = {constraint["name"] for constraint in inspector.get_check_constraints(
            "processing_request_transcripts"
        )}
        self.assertTrue({"start_ms", "end_ms"}.issubset(columns))
        self.assertIn("ck_processing_request_transcript_timing", checks)

    def test_existing_artifact_schema_gains_nullable_ms_columns_without_rewriting_legacy_rows(self) -> None:
        legacy_engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with legacy_engine.begin() as connection:
                connection.execute(text("""
                    CREATE TABLE processing_request_transcripts (
                        id INTEGER PRIMARY KEY,
                        text TEXT NOT NULL,
                        start_seconds FLOAT,
                        end_seconds FLOAT
                    )
                """))
                connection.execute(text("""
                    INSERT INTO processing_request_transcripts (id, text)
                    VALUES (1, 'legacy')
                """))

            schema.ensure_processing_transcript_timing_schema(legacy_engine)

            columns = {
                column["name"]
                for column in inspect(legacy_engine).get_columns("processing_request_transcripts")
            }
            with legacy_engine.connect() as connection:
                row = connection.execute(text("""
                    SELECT text, start_ms, end_ms
                    FROM processing_request_transcripts
                    WHERE id = 1
                """)).one()
            self.assertTrue({"start_seconds", "end_seconds", "start_ms", "end_ms"}.issubset(columns))
            self.assertEqual(tuple(row), ("legacy", None, None))
        finally:
            legacy_engine.dispose()


if __name__ == "__main__":
    unittest.main()
