import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.processing.adapters.youtube_media_source import (
    SubprocessYtDlpCommandRunner,
    YOUTUBE_CANONICAL_WATCH_URL,
    YouTubeProcessingMediaSource,
    YtDlpCommandResult,
)
from app.processing.domain.failures import (
    YouTubeAcquisitionError,
    YouTubeAcquisitionTimeoutError,
    YouTubeDurationLimitExceededError,
    YouTubeGvsForbiddenError,
    YouTubeLiveNotSupportedError,
    YouTubePoTokenProviderUnavailableError,
    YouTubeSizeLimitExceededError,
    YouTubeUnavailableError,
)
from app.processing.domain.models import YouTubeProcessingExecutionCommand


VIDEO_ID = "abc_DEF-123"


def command() -> YouTubeProcessingExecutionCommand:
    return YouTubeProcessingExecutionCommand(
        event_id="event-youtube-1",
        asset_id="asset-youtube-1",
        workspace_id="workspace-1",
        owner_id="owner-1",
        youtube_video_id=VIDEO_ID,
    )


class FakeYtDlpRunner:
    def __init__(
        self,
        *,
        metadata: dict | None = None,
        metadata_result: YtDlpCommandResult | None = None,
        download_result: YtDlpCommandResult | None = None,
        download_results: list[YtDlpCommandResult] | None = None,
        extra_download_file: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.metadata = metadata or {
            "id": VIDEO_ID,
            "duration": 60,
            "live_status": "not_live",
            "title": "../../../../provider-controlled-title",
        }
        self.metadata_result = metadata_result
        self.download_result = download_result
        self.download_results = list(download_results or [])
        self.extra_download_file = extra_download_file
        self.error = error
        self.calls: list[dict] = []
        self.owned_directories: list[Path] = []

    def run(
        self,
        args,
        *,
        timeout_seconds,
        cwd,
        max_directory_size=None,
        output_limit_bytes=64 * 1024,
    ):
        owned_dir = Path(cwd)
        self.calls.append({
            "args": list(args),
            "timeout_seconds": timeout_seconds,
            "cwd": owned_dir,
            "max_directory_size": max_directory_size,
            "output_limit_bytes": output_limit_bytes,
        })
        self.owned_directories.append(owned_dir)
        if self.error is not None:
            raise self.error
        if "--dump-single-json" in args:
            return self.metadata_result or YtDlpCommandResult(
                0,
                json.dumps(self.metadata),
                "",
            )
        if self.download_result is not None:
            return self.download_result
        if self.download_results:
            result = self.download_results.pop(0)
            if result.returncode != 0:
                return result

        media_path = owned_dir / "media.webm"
        media_path.write_bytes(b"downloaded media")
        if self.extra_download_file:
            (owned_dir / "media.info.json").write_text("{}", encoding="utf-8")
        return YtDlpCommandResult(
            0,
            f"SELECTED_FORMAT=251\nOUTPUT={media_path}\n",
            "",
        )


def media_source(runner: FakeYtDlpRunner, **overrides) -> YouTubeProcessingMediaSource:
    options = {
        "max_duration_seconds": 7_200,
        "max_file_size_bytes": 1_024,
        "socket_timeout_seconds": 17,
        "acquisition_timeout_seconds": 120,
        "download_retries": 2,
        "po_token_provider_url": "http://youtube-pot-provider:4416",
        "retry_sleeper": lambda _seconds: None,
        "runner": runner,
    }
    options.update(overrides)
    return YouTubeProcessingMediaSource(**options)


class YouTubeProcessingMediaSourceTest(unittest.TestCase):
    def test_subprocess_boundary_never_uses_a_shell(self) -> None:
        process = MagicMock()
        process.poll.return_value = 0
        process.returncode = 0
        with (
            tempfile.TemporaryDirectory(prefix="youtube_runner_test_") as temp_dir,
            patch(
                "app.processing.adapters.youtube_media_source.subprocess.Popen",
                return_value=process,
            ) as popen,
        ):
            result = SubprocessYtDlpCommandRunner().run(
                ["python", "-m", "yt_dlp", "--version"],
                timeout_seconds=1,
                cwd=Path(temp_dir),
            )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.kwargs["cwd"], Path(temp_dir))

    def test_constructs_only_the_canonical_url_and_uses_a_fixed_safe_output(self) -> None:
        runner = FakeYtDlpRunner()
        source = media_source(runner)
        with source.acquire(command()) as acquired_path:
            acquired = Path(acquired_path)
            attempt_dir = acquired.parent
            owned_dir = attempt_dir.parent
            self.assertTrue(acquired.exists())
            self.assertEqual(acquired.name, "media.webm")
            self.assertIn("youtube_", owned_dir.name)
            self.assertEqual(attempt_dir.name, "attempt-1")
            self.assertNotIn("provider-controlled-title", str(acquired))

        expected_url = YOUTUBE_CANONICAL_WATCH_URL.format(video_id=VIDEO_ID)
        self.assertEqual(len(runner.calls), 2)
        for call in runner.calls:
            args = call["args"]
            self.assertEqual(args[-2:], ["--", expected_url])
            self.assertIn("--ignore-config", args)
            self.assertEqual(args[args.index("--plugin-dirs") + 1], "default")
            self.assertIn("--no-remote-components", args)
            self.assertIn("--no-playlist", args)
            self.assertIn("--no-cookies", args)
            self.assertIn("--no-cookies-from-browser", args)
            extractor_args = [
                args[index + 1]
                for index, value in enumerate(args)
                if value == "--extractor-args"
            ]
            self.assertIn("youtube:player_client=mweb", extractor_args)
            self.assertIn(
                "youtubepot-bgutilhttp:base_url=http://youtube-pot-provider:4416",
                extractor_args,
            )
        download_args = runner.calls[1]["args"]
        output_template = download_args[download_args.index("--output") + 1]
        self.assertEqual(output_template, str(attempt_dir / "media.%(ext)s"))
        self.assertNotIn("title", output_template)
        self.assertFalse(owned_dir.exists())

    def test_socket_timeout_and_every_retry_category_are_bounded(self) -> None:
        runner = FakeYtDlpRunner()
        with media_source(runner).acquire(command()):
            pass
        for call in runner.calls:
            args = call["args"]
            self.assertEqual(args[args.index("--socket-timeout") + 1], "17")
            for option in (
                "--retries",
                "--fragment-retries",
                "--extractor-retries",
                "--file-access-retries",
            ):
                self.assertEqual(args[args.index(option) + 1], "2")
        self.assertEqual(
            runner.calls[1]["max_directory_size"],
            1_024,
        )
        self.assertLessEqual(
            max(call["timeout_seconds"] for call in runner.calls),
            120,
        )

    def test_gvs_403_gets_one_fresh_attempt_and_safe_observable_success(self) -> None:
        signed_url = (
            "https://example.googlevideo.com/videoplayback?sig=do-not-log"
            "&pot=do-not-log"
        )
        runner = FakeYtDlpRunner(
            download_results=[
                YtDlpCommandResult(
                    1,
                    "",
                    f"ERROR: HTTP Error 403: Forbidden {signed_url}",
                )
            ]
        )

        with self.assertLogs(
            "app.processing.adapters.youtube_media_source",
            level="INFO",
        ) as captured:
            with media_source(runner).acquire(command()) as media_path:
                self.assertTrue(Path(media_path).exists())

        self.assertEqual(len(runner.calls), 4)
        self.assertNotEqual(runner.calls[0]["cwd"], runner.calls[2]["cwd"])
        self.assertTrue(
            any("youtube_acquisition_retry" in message for message in captured.output)
        )
        self.assertTrue(
            any("error_family=youtube_gvs_forbidden" in message for message in captured.output)
        )
        self.assertTrue(
            any("format_id=251" in message for message in captured.output)
        )
        safe_logs = "\n".join(captured.output)
        self.assertNotIn("googlevideo.com", safe_logs)
        self.assertNotIn("do-not-log", safe_logs)
        self.assertTrue(all(not directory.exists() for directory in runner.owned_directories))

    def test_provider_unavailable_exhausts_once_without_format18_fallback(self) -> None:
        runner = FakeYtDlpRunner(
            download_result=YtDlpCommandResult(
                1,
                "",
                "ERROR: Could not reach bgutil HTTP server; token=do-not-log",
            )
        )

        with (
            self.assertLogs(
                "app.processing.adapters.youtube_media_source",
                level="WARNING",
            ) as captured,
            self.assertRaises(YouTubePoTokenProviderUnavailableError),
        ):
            with media_source(runner).acquire(command()):
                pass

        self.assertEqual(len(runner.calls), 4)
        download_calls = [call for call in runner.calls if "--format" in call["args"]]
        self.assertEqual(len(download_calls), 2)
        self.assertTrue(
            all(
                call["args"][call["args"].index("--format") + 1]
                == "bestaudio/best"
                for call in download_calls
            )
        )
        safe_logs = "\n".join(captured.output)
        self.assertIn("youtube_pot_provider_unavailable", safe_logs)
        self.assertIn("youtube_acquisition_terminal_failure", safe_logs)
        self.assertNotIn("do-not-log", safe_logs)
        self.assertTrue(all(not directory.exists() for directory in runner.owned_directories))

    def test_gvs_and_provider_failures_keep_the_external_generic_error_code(self) -> None:
        self.assertEqual(YouTubeGvsForbiddenError.code, "YOUTUBE_ACQUISITION_FAILED")
        self.assertEqual(
            YouTubePoTokenProviderUnavailableError.code,
            "YOUTUBE_ACQUISITION_FAILED",
        )

    def test_duration_size_and_live_metadata_are_controlled_failures(self) -> None:
        cases = (
            (
                {"id": VIDEO_ID, "duration": 7_201, "live_status": "not_live"},
                YouTubeDurationLimitExceededError,
            ),
            (
                {
                    "id": VIDEO_ID,
                    "duration": 60,
                    "live_status": "not_live",
                    "filesize_approx": 1_025,
                },
                YouTubeSizeLimitExceededError,
            ),
            (
                {"id": VIDEO_ID, "duration": 60, "is_live": True},
                YouTubeLiveNotSupportedError,
            ),
            (
                {"id": VIDEO_ID, "duration": 60, "live_status": "is_upcoming"},
                YouTubeLiveNotSupportedError,
            ),
        )
        for metadata, expected_error in cases:
            runner = FakeYtDlpRunner(metadata=metadata)
            with (
                self.subTest(metadata=metadata),
                self.assertRaises(expected_error),
            ):
                with media_source(runner).acquire(command()):
                    pass
            self.assertTrue(runner.owned_directories)
            self.assertFalse(runner.owned_directories[0].exists())

    def test_private_unavailable_and_timeout_are_sanitized_classifications(self) -> None:
        unavailable = FakeYtDlpRunner(
            metadata_result=YtDlpCommandResult(
                1,
                "",
                "ERROR: Private video. Sign in with cookies and secret=do-not-return",
            )
        )
        with self.assertRaises(YouTubeUnavailableError) as unavailable_context:
            with media_source(unavailable).acquire(command()):
                pass
        self.assertEqual(
            str(unavailable_context.exception),
            "YouTube video is unavailable for public unauthenticated acquisition",
        )
        self.assertEqual(len(unavailable.calls), 1)
        self.assertFalse(unavailable.owned_directories[0].exists())

        removed = FakeYtDlpRunner(
            metadata_result=YtDlpCommandResult(
                1,
                "",
                "ERROR: This video is unavailable",
            )
        )
        with self.assertRaises(YouTubeUnavailableError):
            with media_source(removed).acquire(command()):
                pass
        self.assertEqual(len(removed.calls), 1)
        self.assertFalse(removed.owned_directories[0].exists())

        timeout = FakeYtDlpRunner(error=YouTubeAcquisitionTimeoutError())
        with self.assertRaises(YouTubeAcquisitionTimeoutError):
            with media_source(timeout).acquire(command()):
                pass
        self.assertFalse(timeout.owned_directories[0].exists())

    def test_ambiguous_or_oversized_download_is_rejected_and_cleaned(self) -> None:
        ambiguous = FakeYtDlpRunner(extra_download_file=True)
        with self.assertRaises(YouTubeAcquisitionError):
            with media_source(ambiguous).acquire(command()):
                pass
        self.assertFalse(ambiguous.owned_directories[0].exists())

        class OversizeRunner(FakeYtDlpRunner):
            def run(self, args, **kwargs):
                if "--dump-single-json" in args:
                    return super().run(args, **kwargs)
                owned_dir = Path(kwargs["cwd"])
                self.calls.append({"args": list(args), **kwargs})
                self.owned_directories.append(owned_dir)
                media_path = owned_dir / "media.webm"
                media_path.write_bytes(b"x" * 1_025)
                return YtDlpCommandResult(
                    0,
                    f"SELECTED_FORMAT=251\nOUTPUT={media_path}\n",
                    "",
                )

        oversized = OversizeRunner()
        with self.assertRaises(YouTubeSizeLimitExceededError):
            with media_source(oversized).acquire(command()):
                pass
        self.assertFalse(oversized.owned_directories[0].exists())

    def test_download_failure_and_caller_exception_both_clean_all_files(self) -> None:
        failed = FakeYtDlpRunner(
            download_result=YtDlpCommandResult(1, "", "network failed after retries")
        )
        with self.assertRaises(YouTubeAcquisitionError):
            with media_source(failed).acquire(command()):
                pass
        self.assertFalse(failed.owned_directories[0].exists())

        successful = FakeYtDlpRunner()
        with self.assertRaisesRegex(RuntimeError, "transcriber cancelled"):
            with media_source(successful).acquire(command()):
                raise RuntimeError("transcriber cancelled")
        self.assertFalse(successful.owned_directories[0].exists())

        cancelled = FakeYtDlpRunner()
        with self.assertRaises(KeyboardInterrupt):
            with media_source(cancelled).acquire(command()):
                raise KeyboardInterrupt()
        self.assertTrue(
            all(not directory.exists() for directory in cancelled.owned_directories)
        )

    def test_output_outside_owned_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="youtube_test_outside_") as outside:
            outside_path = Path(outside) / "media.webm"
            outside_path.write_bytes(b"outside")
            runner = FakeYtDlpRunner(
                download_result=YtDlpCommandResult(
                    0,
                    f"SELECTED_FORMAT=251\nOUTPUT={outside_path}\n",
                    "",
                )
            )
            with self.assertRaises(YouTubeAcquisitionError):
                with media_source(runner).acquire(command()):
                    pass
            self.assertFalse(runner.owned_directories[0].exists())


if __name__ == "__main__":
    unittest.main()
