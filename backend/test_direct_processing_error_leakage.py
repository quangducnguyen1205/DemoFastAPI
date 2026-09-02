"""The legacy direct-upload surface must not hand internal detail to its caller.

Failures on this path carry exactly the material an external response should never contain:
``subprocess.CalledProcessError`` stringifies to the whole ffmpeg argument vector including
absolute media and temp paths, and a database error stringifies to SQL. These tests drive the
real ASGI application and the real application service, so they fail if either one starts
returning the exception text again.
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from app.bootstrap.api import create_api_app
from app.config.settings import settings
from app.processing.application.execute import ExecuteDirectUploadProcessingApplicationService
from app.tasks.video_tasks import process_video_task
from test_internal_api_security import perform_request

# What a real audio-extraction failure looks like when it is stringified.
FFMPEG_FAILURE = subprocess.CalledProcessError(
    1,
    [
        "ffmpeg", "-y", "-i", "/backend/media/videos/learner-1/lesson.mp4",
        "-vn", "-ac", "1", "-ar", "16000", "/tmp/tmp9s7d/audio.wav",
    ],
)
LEAKED_FRAGMENTS = (
    "/backend/media",
    "/tmp/tmp9s7d",
    "ffmpeg",
    "CalledProcessError",
    "non-zero exit status",
)


class DirectUploadFailureResponseTest(unittest.TestCase):
    def _service(self):
        store = MagicMock()
        store.exists.return_value = True
        transcriber = MagicMock()
        transcriber.transcribe.side_effect = FFMPEG_FAILURE
        return ExecuteDirectUploadProcessingApplicationService(
            transcriber=transcriber, artifact_store=store
        ), store

    def test_a_failed_direct_upload_returns_a_stable_code_not_the_exception_text(self) -> None:
        service, store = self._service()

        with self.assertLogs("app.processing.application.execute", level="ERROR") as logs:
            result = service.execute(video_id=1, media_path="/backend/media/videos/lesson.mp4")

        self.assertEqual(result["status"], "failed")
        for fragment in LEAKED_FRAGMENTS:
            self.assertNotIn(fragment, result["error"], f"{fragment!r} leaked into the response")
        store.persist_failed.assert_called_once_with(1)
        # The detail is not lost: it stays in the worker log where an operator can read it.
        self.assertIn("ffmpeg", "\n".join(logs.output))

    def test_the_stable_code_is_the_same_for_every_unexpected_failure(self) -> None:
        first, _ = self._service()
        second_store = MagicMock()
        second_store.exists.return_value = True
        second_transcriber = MagicMock()
        second_transcriber.transcribe.side_effect = RuntimeError(
            "could not connect to server: /var/run/postgresql/.s.PGSQL.5432"
        )
        second = ExecuteDirectUploadProcessingApplicationService(
            transcriber=second_transcriber, artifact_store=second_store
        )

        with self.assertLogs("app.processing.application.execute", level="ERROR"):
            first_result = first.execute(video_id=1, media_path="/m.mp4")
        with self.assertLogs("app.processing.application.execute", level="ERROR"):
            second_result = second.execute(video_id=2, media_path="/m.mp4")

        self.assertEqual(first_result["error"], second_result["error"])
        self.assertNotIn("PGSQL", second_result["error"])

    def test_a_missing_video_keeps_its_existing_safe_message(self) -> None:
        store = MagicMock()
        store.exists.return_value = False
        service = ExecuteDirectUploadProcessingApplicationService(
            transcriber=MagicMock(), artifact_store=store
        )

        result = service.execute(video_id=7, media_path="/m.mp4")

        self.assertEqual(result, {"status": "failed", "error": "Video 7 not found"})


class TaskStatusResponseTest(unittest.TestCase):
    def setUp(self):
        self.app = create_api_app()
        # The standalone loopback default leaves this surface open, which is the exposed case.
        patcher = patch.object(settings, "INTERNAL_API_AUTH_ENABLED", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _status_body(self, state, result):
        async_result = MagicMock()
        async_result.state = state
        async_result.result = result
        with patch.object(process_video_task, "AsyncResult", return_value=async_result):
            status, _headers, body = perform_request(
                self.app, "GET", "/videos/tasks/2b1f9c00-0000-4000-8000-000000000001"
            )
        self.assertEqual(status, 200)
        return body.decode("utf-8")

    def test_a_failed_celery_task_does_not_return_the_raw_exception(self) -> None:
        body = self._status_body("FAILURE", FFMPEG_FAILURE)

        for fragment in LEAKED_FRAGMENTS:
            self.assertNotIn(fragment, body, f"{fragment!r} leaked through /videos/tasks")

    def test_a_succeeded_task_payload_carries_no_internal_paths(self) -> None:
        body = self._status_body(
            "SUCCESS", {"status": "failed", "error": "PROCESSING_FAILED"}
        )

        self.assertIn("PROCESSING_FAILED", body)
        for fragment in ("/backend/media", "/tmp/"):
            self.assertNotIn(fragment, body)

    def test_a_pending_task_still_reports_its_state(self) -> None:
        body = self._status_body("PENDING", None)

        self.assertIn("PENDING", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
