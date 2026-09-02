"""Execution bounds for the processing leg.

The point of these tests is that the bounds are real: a genuinely hanging child process is
killed, and a genuinely blocking in-process transcription is interrupted by the same kind of
signal-delivered exception Celery's soft time limit raises. Tests that merely assert
``raise TimeoutError`` would prove nothing about either.
"""

import importlib
import os
import signal
import subprocess
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from celery.exceptions import SoftTimeLimitExceeded

import app.config.settings as settings_module
from app.config.settings import settings
from app.core.celery_app import celery_app
from app.processing.adapters.whisper_transcriber import WhisperProcessingTranscriptionProvider
from app.processing.application.execute import (
    ExecuteDirectUploadProcessingApplicationService,
    ExecuteProcessingApplicationService,
)
from app.processing.domain.failures import MediaTranscodingTimeoutError
from app.processing.domain.models import (
    ProcessingExecutionCommand,
    ProcessingFailed,
    ProcessingLease,
    ProcessingSucceeded,
)
from app.services.video_processing import extract_audio_to_wav


def command() -> ProcessingExecutionCommand:
    return ProcessingExecutionCommand(
        event_id="event-1",
        asset_id="asset-1",
        workspace_id=None,
        owner_id=None,
        storage_bucket="workspace-media",
        object_key="objects/media.mp4",
        original_filename=None,
        content_type="video/mp4",
        size_bytes=128,
    )


class FakeMediaSource:
    def acquire(self, _command):
        class _Acquired:
            def __enter__(self):
                return "/tmp/media.mp4"

            def __exit__(self, *_args):
                return False

        return _Acquired()


def process_is_gone(pid: int, *, within_seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + within_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:  # pragma: no cover - not expected in the test sandbox
            return False
        time.sleep(0.05)
    return False


class FakeFfmpegOnPath:
    """Puts a controllable executable named ``ffmpeg`` ahead of the real one on PATH."""

    def __init__(self, script_body: str) -> None:
        self._script_body = script_body
        self._directory = tempfile.TemporaryDirectory(prefix="fake_ffmpeg_")
        self._original_path = os.environ.get("PATH", "")

    def __enter__(self) -> "FakeFfmpegOnPath":
        executable = Path(self._directory.name) / "ffmpeg"
        executable.write_text(self._script_body)
        executable.chmod(0o755)
        os.environ["PATH"] = f"{self._directory.name}{os.pathsep}{self._original_path}"
        return self

    def __exit__(self, *_args) -> bool:
        os.environ["PATH"] = self._original_path
        self._directory.cleanup()
        return False


class FfmpegSubprocessBoundTests(unittest.TestCase):
    def test_a_hanging_extraction_child_is_killed_and_reported_as_a_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as work_dir:
            pid_file = Path(work_dir) / "child.pid"
            # `exec` replaces the shell, so the recorded pid is the process that actually sleeps.
            hanging = f'#!/bin/sh\necho $$ > "{pid_file}"\nexec sleep 30\n'
            with FakeFfmpegOnPath(hanging):
                with tempfile.TemporaryDirectory() as temp_dir:
                    started_at = time.monotonic()
                    with self.assertRaises(MediaTranscodingTimeoutError) as raised:
                        extract_audio_to_wav("/tmp/media.mp4", temp_dir, timeout_seconds=0.75)
                    elapsed = time.monotonic() - started_at

                    self.assertLess(elapsed, 10, "the bound must fire long before the child ends")
                    self.assertIn("timeout", str(raised.exception).lower())
                    self.assertFalse(
                        (Path(temp_dir) / "audio.wav").exists(),
                        "a timed-out extraction must not leave a usable artifact",
                    )

                recorded_pid = int(pid_file.read_text().strip())
            self.assertTrue(
                process_is_gone(recorded_pid),
                "the extraction child must not survive its own timeout",
            )
            self.assertFalse(Path(temp_dir).exists(), "the temporary directory is unwound")

    def test_an_extraction_that_finishes_within_the_bound_still_succeeds(self) -> None:
        quick = '#!/bin/sh\nsleep 0.05\ntouch "$6"\n'
        with FakeFfmpegOnPath(quick):
            with tempfile.TemporaryDirectory() as temp_dir:
                audio_path = extract_audio_to_wav("/tmp/media.mp4", temp_dir, timeout_seconds=5)
                self.assertEqual(audio_path, os.path.join(temp_dir, "audio.wav"))

    def test_the_configured_bound_is_used_when_no_explicit_timeout_is_given(self) -> None:
        with patch("app.services.video_processing.subprocess.run") as run:
            with tempfile.TemporaryDirectory() as temp_dir:
                extract_audio_to_wav("/tmp/media.mp4", temp_dir)
        self.assertEqual(run.call_args.kwargs["timeout"], settings.FFMPEG_TIMEOUT_SECONDS)


class CeleryExecutionBoundTests(unittest.TestCase):
    def test_the_worker_applies_the_configured_soft_and_hard_task_limits(self) -> None:
        self.assertEqual(
            celery_app.conf.task_soft_time_limit,
            settings.PROCESSING_SOFT_TIME_LIMIT_SECONDS,
        )
        self.assertEqual(
            celery_app.conf.task_time_limit,
            settings.PROCESSING_HARD_TIME_LIMIT_SECONDS,
        )

    def test_the_bounds_are_ordered_so_each_layer_can_act_before_the_next(self) -> None:
        self.assertLess(
            settings.FFMPEG_TIMEOUT_SECONDS,
            settings.PROCESSING_SOFT_TIME_LIMIT_SECONDS,
        )
        self.assertLess(
            settings.PROCESSING_SOFT_TIME_LIMIT_SECONDS,
            settings.PROCESSING_HARD_TIME_LIMIT_SECONDS,
        )
        self.assertLess(
            settings.PROCESSING_HARD_TIME_LIMIT_SECONDS,
            settings.PROCESSING_LEASE_SECONDS,
            "a killed attempt must still hold a lease that expires afterwards",
        )

    def test_the_longest_supported_media_fits_inside_the_attempt_bound(self) -> None:
        acquisition_and_extraction = (
            settings.YOUTUBE_ACQUISITION_TIMEOUT_SECONDS + settings.FFMPEG_TIMEOUT_SECONDS
        )
        self.assertGreater(
            settings.PROCESSING_SOFT_TIME_LIMIT_SECONDS,
            settings.YOUTUBE_MAX_DURATION_SECONDS + acquisition_and_extraction,
            "legitimate media at the supported maximum must not be cut off",
        )


class BrokerVisibilityBoundTests(unittest.TestCase):
    """The broker's delivery bound and the worker's execution bound must agree.

    Redis emulates acknowledgement: kombu.transport.redis.QoS.append stamps a delivery once and
    restore_visible() puts anything older than the visibility timeout back on the queue. Nothing
    refreshes that stamp while a task executes, so a visibility timeout shorter than an attempt
    redelivers healthy work in flight.
    """

    def _load_settings(self, overrides: dict[str, str]):
        environment = {"DOTENV_PATH": "/tmp/nonexistent-project3-env", **overrides}
        try:
            with patch.dict(os.environ, environment, clear=True):
                return importlib.reload(settings_module).settings
        finally:
            importlib.reload(settings_module)

    def test_visibility_outlasts_the_longest_attempt_a_worker_may_hold(self) -> None:
        self.assertGreater(
            settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
            settings.PROCESSING_HARD_TIME_LIMIT_SECONDS,
            "a healthy attempt must never have its own delivery restored while it runs",
        )

    def test_visibility_expires_before_the_lease_it_has_to_hand_back(self) -> None:
        self.assertLess(
            settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
            settings.PROCESSING_LEASE_SECONDS,
            "a lost worker's delivery must be queued again before its lease expires, "
            "so the lease and not the broker sets the recovery latency",
        )

    def test_every_transport_surface_carries_the_same_visibility_bound(self) -> None:
        expected = settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS
        self.assertEqual(
            celery_app.conf.broker_transport_options["visibility_timeout"], expected
        )
        self.assertEqual(
            celery_app.conf.result_backend_transport_options["visibility_timeout"], expected
        )
        self.assertEqual(celery_app.conf.visibility_timeout, expected)

    def test_a_visibility_shorter_than_the_hard_limit_fails_configuration(self) -> None:
        # 3600 is Redis' own default and was this service's value before the execution bounds
        # existed; with them it lets a healthy attempt be redelivered after one hour.
        with self.assertRaises(ValueError):
            self._load_settings({"CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS": "3600"})

    def test_a_visibility_outlasting_the_lease_fails_configuration(self) -> None:
        for value in ("14400", "20000"):
            with self.subTest(visibility=value):
                with self.assertRaises(ValueError):
                    self._load_settings({"CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS": value})

    def test_raising_the_execution_bounds_alone_cannot_silently_pass_the_visibility_bound(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            self._load_settings(
                {
                    "PROCESSING_SOFT_TIME_LIMIT_SECONDS": "12600",
                    "PROCESSING_HARD_TIME_LIMIT_SECONDS": "13000",
                }
            )


class interrupt_after:
    """Delivers a real signal that raises Celery's own soft-limit exception.

    Celery bounds a running task by signalling the worker child, whose handler raises
    ``SoftTimeLimitExceeded`` inside whatever the task is doing. This reproduces that delivery
    with an interval timer so a genuinely blocking call can be interrupted in milliseconds
    instead of hours.
    """

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self._previous_handler = None

    def __enter__(self) -> "interrupt_after":
        def raise_soft_limit(_signum, _frame):
            raise SoftTimeLimitExceeded()

        self._previous_handler = signal.signal(signal.SIGALRM, raise_soft_limit)
        signal.setitimer(signal.ITIMER_REAL, self._seconds)
        return self

    def __exit__(self, *_args) -> bool:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if self._previous_handler is not None:
            signal.signal(signal.SIGALRM, self._previous_handler)
        return False


def blocking_transcribe(*_args, **_kwargs):
    while True:
        time.sleep(0.02)


class HangingTranscriptionOutcomeTests(unittest.TestCase):
    def build_service(self):
        store = MagicMock()
        sink = MagicMock()
        fixed_now = datetime(2026, 9, 2, tzinfo=UTC)
        store.claim.return_value = ProcessingLease(1, fixed_now, fixed_now + timedelta(hours=4))
        service = ExecuteProcessingApplicationService(
            media_source=FakeMediaSource(),
            transcriber=WhisperProcessingTranscriptionProvider(),
            artifact_store=store,
            result_sink=sink,
            clock=lambda: fixed_now,
        )
        return service, store, sink

    def whisper_model(self, model):
        return patch.multiple(
            "app.processing.adapters.whisper_transcriber",
            extract_audio_to_wav=MagicMock(return_value="/tmp/audio.wav"),
        ), patch("app.services.video_processing.get_whisper_model", return_value=model)

    def run_with_hanging_model(self, service, *, seconds: float = 0.4):
        model = MagicMock()
        model.transcribe.side_effect = blocking_transcribe
        extraction, whisper = self.whisper_model(model)
        with extraction, whisper, interrupt_after(seconds):
            return service.execute(command(), task_id="task-1")

    def test_a_transcription_that_never_returns_is_interrupted_and_fails_once(self) -> None:
        service, store, sink = self.build_service()

        started_at = time.monotonic()
        outcome = self.run_with_hanging_model(service)
        elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 10, "the hanging call must actually be interrupted")
        self.assertIsInstance(outcome, ProcessingFailed)
        self.assertEqual(outcome.failure.code, "PROCESSING_FAILED")
        store.rollback.assert_called_once_with()
        store.persist_failure.assert_called_once_with(outcome, attempt_count=1)
        sink.record.assert_called_once_with(outcome)
        store.commit.assert_called_once_with()

    def test_an_interrupted_attempt_can_never_become_a_success(self) -> None:
        service, store, sink = self.build_service()

        outcome = self.run_with_hanging_model(service)

        self.assertNotIsInstance(outcome, ProcessingSucceeded)
        store.persist_success.assert_not_called()
        recorded = [call.args[0] for call in sink.record.call_args_list]
        self.assertEqual(len(recorded), 1, "exactly one result is published")
        self.assertTrue(all(isinstance(item, ProcessingFailed) for item in recorded))

    def test_the_timeout_is_terminal_rather_than_an_endless_retry(self) -> None:
        service, store, sink = self.build_service()

        outcome = self.run_with_hanging_model(service)

        # A terminal failure clears the lease through persist_failure; nothing asks for a retry.
        self.assertIsInstance(outcome, ProcessingFailed)
        store.persist_failure.assert_called_once()
        self.assertEqual(store.claim.call_count, 1)

    def test_the_worker_can_process_another_attempt_after_a_timed_out_one(self) -> None:
        service, store, sink = self.build_service()
        self.run_with_hanging_model(service)

        healthy_model = MagicMock()
        healthy_model.transcribe.return_value = {
            "segments": [{"text": "first", "start": 0.0, "end": 1.25}]
        }
        extraction, whisper = self.whisper_model(healthy_model)
        with extraction, whisper:
            second = service.execute(command(), task_id="task-2")

        self.assertIsInstance(second, ProcessingSucceeded)
        self.assertEqual([row.text for row in second.artifact.rows], ["first"])
        store.persist_success.assert_called_once_with(second, attempt_count=1)

    def test_a_transcription_within_the_bound_still_succeeds(self) -> None:
        service, store, _ = self.build_service()
        model = MagicMock()

        def slow_but_finite(*_args, **_kwargs):
            time.sleep(0.05)
            return {"segments": [{"text": "first", "start": 0.0, "end": 1.0}]}

        model.transcribe.side_effect = slow_but_finite
        extraction, whisper = self.whisper_model(model)
        with extraction, whisper, interrupt_after(5):
            outcome = service.execute(command(), task_id="task-1")

        self.assertIsInstance(outcome, ProcessingSucceeded)
        store.persist_success.assert_called_once()


class DirectUploadBoundTests(unittest.TestCase):
    def test_the_direct_upload_path_is_bounded_by_the_same_mechanism(self) -> None:
        store = MagicMock()
        store.exists.return_value = True
        service = ExecuteDirectUploadProcessingApplicationService(
            transcriber=WhisperProcessingTranscriptionProvider(),
            artifact_store=store,
        )
        model = MagicMock()
        model.transcribe.side_effect = blocking_transcribe

        with (
            patch(
                "app.processing.adapters.whisper_transcriber.extract_audio_to_wav",
                return_value="/tmp/audio.wav",
            ),
            patch("app.services.video_processing.get_whisper_model", return_value=model),
            interrupt_after(0.4),
        ):
            result = service.execute(video_id=1, media_path="/tmp/media.mp4")

        self.assertEqual(result["status"], "failed")
        store.persist_ready.assert_not_called()
        store.persist_failed.assert_called_once_with(1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
