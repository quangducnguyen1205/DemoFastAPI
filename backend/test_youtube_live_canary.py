from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.processing.domain.failures import YouTubeGvsForbiddenError
from app.tools import youtube_live_canary


class _SuccessfulSource:
    def __init__(self, **kwargs) -> None:
        self._runner = kwargs["runner"]

    @contextmanager
    def acquire(self, _command):
        self._runner.metadata_success = True
        with tempfile.TemporaryDirectory(prefix="youtube_canary_test_") as temp_dir:
            media_path = Path(temp_dir) / "media.webm"
            media_path.write_bytes(b"real media bytes")
            yield str(media_path)


class _FailedSource:
    def __init__(self, **kwargs) -> None:
        self._runner = kwargs["runner"]

    @contextmanager
    def acquire(self, _command):
        self._runner.metadata_success = True
        raise YouTubeGvsForbiddenError()
        yield  # pragma: no cover


class YouTubeLiveCanaryTest(unittest.TestCase):
    def test_explicit_live_acknowledgement_is_required(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            exit_code = youtube_live_canary.main([])
        self.assertEqual(exit_code, 2)
        self.assertIn("Refusing to run", stderr.getvalue())

    def test_success_reports_metadata_bytes_and_cleanup_without_secrets(self) -> None:
        stdout = StringIO()
        with (
            patch.object(
                youtube_live_canary,
                "YouTubeProcessingMediaSource",
                _SuccessfulSource,
            ),
            redirect_stdout(stdout),
        ):
            exit_code = youtube_live_canary.main(
                ["--live-network-test", "--video-id", "abc_DEF-123"]
            )

        self.assertEqual(exit_code, 0)
        record = json.loads(stdout.getvalue().splitlines()[1])
        self.assertTrue(record["metadata_success"])
        self.assertTrue(record["media_bytes_success"])
        self.assertTrue(record["temporary_media_cleaned"])
        self.assertGreater(record["bytes_read"], 0)
        self.assertNotIn("token", stdout.getvalue().lower())
        self.assertNotIn("googlevideo", stdout.getvalue().lower())

    def test_metadata_only_success_is_a_nonzero_media_failure(self) -> None:
        stdout = StringIO()
        with (
            patch.object(
                youtube_live_canary,
                "YouTubeProcessingMediaSource",
                _FailedSource,
            ),
            redirect_stdout(stdout),
        ):
            exit_code = youtube_live_canary.main(
                ["--live-network-test", "--video-id", "abc_DEF-123"]
            )

        self.assertEqual(exit_code, 1)
        record = json.loads(stdout.getvalue().splitlines()[1])
        self.assertTrue(record["metadata_success"])
        self.assertFalse(record["media_bytes_success"])
        self.assertEqual(record["safe_error_family"], "youtube_gvs_forbidden")


if __name__ == "__main__":
    unittest.main()
