from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable, Protocol

from app.processing.domain.failures import (
    YouTubeAcquisitionError,
    YouTubeAcquisitionTimeoutError,
    YouTubeDurationLimitExceededError,
    YouTubeGvsForbiddenError,
    YouTubeLiveNotSupportedError,
    YouTubeNetworkTransientError,
    YouTubePoTokenProviderUnavailableError,
    YouTubeRateLimitedError,
    YouTubeSizeLimitExceededError,
    YouTubeUnavailableError,
)
from app.processing.domain.models import YouTubeProcessingExecutionCommand


YOUTUBE_CANONICAL_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
YOUTUBE_ACQUISITION_STRATEGY = "mweb_bgutil_http"
_METADATA_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
_COMMAND_OUTPUT_LIMIT_BYTES = 64 * 1024
_FRESH_ACQUISITION_ATTEMPTS = 2
_FRESH_RETRY_BACKOFF_SECONDS = 1.0
_UNAVAILABLE_MARKERS = (
    "private video",
    "video unavailable",
    "video is unavailable",
    "live stream recording is not available",
    "video has been removed",
    "not available in your country",
    "geo restricted",
    "age-restricted",
    "sign in to confirm your age",
    "members-only",
    "copyright",
)
_PROVIDER_UNAVAILABLE_MARKERS = (
    "could not reach bgutil http server",
    "bgutil http server is not available",
    "error reaching post /get_pot",
    "http error reaching get /ping",
    "unknown error reaching get /ping",
    "error reaching po token provider",
    "po token provider is not available",
    "po token provider unavailable",
)
_NETWORK_TRANSIENT_MARKERS = (
    "connection reset",
    "connection refused",
    "connection aborted",
    "temporary failure",
    "network is unreachable",
    "remote end closed connection",
    "timed out",
    "timeout",
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YtDlpCommandResult:
    returncode: int
    stdout: str
    stderr: str


class YtDlpCommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        cwd: Path,
        max_directory_size: int | None = None,
        output_limit_bytes: int = _COMMAND_OUTPUT_LIMIT_BYTES,
    ) -> YtDlpCommandResult:
        ...


class SubprocessYtDlpCommandRunner:
    _POLL_INTERVAL_SECONDS = 0.1

    def run(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        cwd: Path,
        max_directory_size: int | None = None,
        output_limit_bytes: int = _COMMAND_OUTPUT_LIMIT_BYTES,
    ) -> YtDlpCommandResult:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
                start_new_session=os.name == "posix",
            )
            deadline = time.monotonic() + timeout_seconds
            try:
                while process.poll() is None:
                    if (
                        os.fstat(stdout_file.fileno()).st_size > output_limit_bytes
                        or os.fstat(stderr_file.fileno()).st_size > output_limit_bytes
                    ):
                        self._terminate(process)
                        raise YouTubeAcquisitionError()
                    if (
                        max_directory_size is not None
                        and _directory_size_bytes(cwd) > max_directory_size
                    ):
                        self._terminate(process)
                        raise YouTubeSizeLimitExceededError()
                    if time.monotonic() >= deadline:
                        self._terminate(process)
                        raise YouTubeAcquisitionTimeoutError()
                    time.sleep(self._POLL_INTERVAL_SECONDS)
            except BaseException:
                if process.poll() is None:
                    self._terminate(process)
                raise

            stdout = _read_bounded_text(stdout_file, output_limit_bytes)
            stderr = _read_bounded_text(stderr_file, output_limit_bytes)
            return YtDlpCommandResult(process.returncode, stdout, stderr)

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:  # pragma: no cover - the supported runtime image is Linux
                process.terminate()
        except ProcessLookupError:
            process.wait()
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:  # pragma: no cover - the supported runtime image is Linux
                process.kill()
            process.wait()


class YouTubeProcessingMediaSource:
    def __init__(
        self,
        *,
        max_duration_seconds: int,
        max_file_size_bytes: int,
        socket_timeout_seconds: int,
        acquisition_timeout_seconds: int,
        download_retries: int,
        po_token_provider_url: str,
        runner: YtDlpCommandRunner | None = None,
        retry_sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._max_duration_seconds = max_duration_seconds
        self._max_file_size_bytes = max_file_size_bytes
        self._socket_timeout_seconds = socket_timeout_seconds
        self._acquisition_timeout_seconds = acquisition_timeout_seconds
        self._download_retries = download_retries
        self._po_token_provider_url = po_token_provider_url
        self._runner = runner or SubprocessYtDlpCommandRunner()
        self._retry_sleeper = retry_sleeper or time.sleep

    @contextmanager
    def acquire(self, command: YouTubeProcessingExecutionCommand):
        prefix_identity = f"{command.event_id}:{command.asset_id}".encode("utf-8")
        safe_prefix = hashlib.sha256(prefix_identity).hexdigest()[:16]
        with tempfile.TemporaryDirectory(prefix=f"youtube_{safe_prefix}_") as temp_dir:
            owned_dir = Path(temp_dir).resolve()
            deadline = time.monotonic() + self._acquisition_timeout_seconds
            canonical_url = YOUTUBE_CANONICAL_WATCH_URL.format(
                video_id=command.youtube_video_id
            )
            logger.info(
                "youtube_acquisition_strategy video_id=%s strategy=%s max_attempts=%s",
                command.youtube_video_id,
                YOUTUBE_ACQUISITION_STRATEGY,
                _FRESH_ACQUISITION_ATTEMPTS,
            )
            media_path: Path | None = None
            for attempt in range(1, _FRESH_ACQUISITION_ATTEMPTS + 1):
                attempt_dir = owned_dir / f"attempt-{attempt}"
                attempt_dir.mkdir()
                try:
                    metadata = self._read_metadata(
                        command,
                        canonical_url=canonical_url,
                        owned_dir=attempt_dir,
                        deadline=deadline,
                        attempt=attempt,
                    )
                    self._validate_metadata(command, metadata)
                    media_path, selected_format = self._download(
                        video_id=command.youtube_video_id,
                        canonical_url=canonical_url,
                        owned_dir=attempt_dir,
                        deadline=deadline,
                        attempt=attempt,
                    )
                    logger.info(
                        "youtube_acquisition_success video_id=%s strategy=%s attempt=%s format_id=%s",
                        command.youtube_video_id,
                        YOUTUBE_ACQUISITION_STRATEGY,
                        attempt,
                        selected_format,
                    )
                    break
                except YouTubeAcquisitionError as exc:
                    if exc.retryable and attempt < _FRESH_ACQUISITION_ATTEMPTS:
                        logger.warning(
                            "youtube_acquisition_retry video_id=%s strategy=%s attempt=%s error_family=%s",
                            command.youtube_video_id,
                            YOUTUBE_ACQUISITION_STRATEGY,
                            attempt,
                            exc.diagnostic_family,
                        )
                        shutil.rmtree(attempt_dir, ignore_errors=True)
                        remaining = _remaining_timeout(deadline)
                        self._retry_sleeper(
                            min(_FRESH_RETRY_BACKOFF_SECONDS, remaining)
                        )
                        continue
                    logger.warning(
                        "youtube_acquisition_terminal_failure video_id=%s strategy=%s attempt=%s error_family=%s",
                        command.youtube_video_id,
                        YOUTUBE_ACQUISITION_STRATEGY,
                        attempt,
                        exc.diagnostic_family,
                    )
                    raise
            if media_path is None:  # pragma: no cover - every loop path succeeds or raises
                raise YouTubeAcquisitionError()
            yield str(media_path)

    def _base_args(self) -> list[str]:
        retries = str(self._download_retries)
        return [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--plugin-dirs",
            "default",
            "--no-remote-components",
            "--no-cache-dir",
            "--no-cookies",
            "--no-cookies-from-browser",
            "--no-playlist",
            "--abort-on-error",
            "--no-wait-for-video",
            "--no-progress",
            "--socket-timeout",
            str(self._socket_timeout_seconds),
            "--retries",
            retries,
            "--fragment-retries",
            retries,
            "--extractor-retries",
            retries,
            "--file-access-retries",
            retries,
            "--extractor-args",
            "youtube:player_client=mweb",
            "--extractor-args",
            f"youtubepot-bgutilhttp:base_url={self._po_token_provider_url}",
        ]

    def _read_metadata(
        self,
        command: YouTubeProcessingExecutionCommand,
        *,
        canonical_url: str,
        owned_dir: Path,
        deadline: float,
        attempt: int,
    ) -> dict:
        result = self._runner.run(
            [
                *self._base_args(),
                "--skip-download",
                "--dump-single-json",
                "--",
                canonical_url,
            ],
            timeout_seconds=_remaining_timeout(deadline),
            cwd=owned_dir,
            output_limit_bytes=_METADATA_OUTPUT_LIMIT_BYTES,
        )
        self._raise_for_provider_diagnostic(
            result,
            video_id=command.youtube_video_id,
            attempt=attempt,
        )
        self._raise_for_command_failure(result)
        try:
            metadata = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise YouTubeAcquisitionError() from exc
        if not isinstance(metadata, dict) or metadata.get("id") != command.youtube_video_id:
            raise YouTubeAcquisitionError()
        return metadata

    def _validate_metadata(
        self,
        _command: YouTubeProcessingExecutionCommand,
        metadata: dict,
    ) -> None:
        if metadata.get("is_live") is True or metadata.get("live_status") in {
            "is_live",
            "is_upcoming",
            "post_live",
        }:
            raise YouTubeLiveNotSupportedError()

        duration = metadata.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise YouTubeAcquisitionError()
        if duration < 0:
            raise YouTubeAcquisitionError()
        if duration > self._max_duration_seconds:
            raise YouTubeDurationLimitExceededError()

        for field in ("filesize", "filesize_approx"):
            value = metadata.get(field)
            if isinstance(value, bool):
                raise YouTubeAcquisitionError()
            if isinstance(value, (int, float)) and value > self._max_file_size_bytes:
                raise YouTubeSizeLimitExceededError()

    def _download(
        self,
        *,
        video_id: str,
        canonical_url: str,
        owned_dir: Path,
        deadline: float,
        attempt: int,
    ) -> tuple[Path, str]:
        output_template = str(owned_dir / "media.%(ext)s")
        result = self._runner.run(
            [
                *self._base_args(),
                "--format",
                "bestaudio/best",
                "--max-filesize",
                str(self._max_file_size_bytes),
                "--output",
                output_template,
                "--print",
                "before_dl:SELECTED_FORMAT=%(format_id)s",
                "--print",
                "after_move:OUTPUT=%(filepath)s",
                "--no-simulate",
                "--",
                canonical_url,
            ],
            timeout_seconds=_remaining_timeout(deadline),
            cwd=owned_dir,
            max_directory_size=self._max_file_size_bytes,
        )
        self._raise_for_provider_diagnostic(
            result,
            video_id=video_id,
            attempt=attempt,
        )
        self._raise_for_command_failure(result)
        return _select_downloaded_file(
            owned_dir,
            result.stdout,
            max_file_size_bytes=self._max_file_size_bytes,
        )

    @staticmethod
    def _raise_for_command_failure(result: YtDlpCommandResult) -> None:
        if result.returncode == 0:
            return
        normalized_error = result.stderr.lower()
        if "max-filesize" in normalized_error or "larger than" in normalized_error:
            raise YouTubeSizeLimitExceededError()
        if any(marker in normalized_error for marker in _UNAVAILABLE_MARKERS):
            raise YouTubeUnavailableError()
        if _provider_is_unavailable(normalized_error):
            raise YouTubePoTokenProviderUnavailableError()
        if "http error 403" in normalized_error or "403: forbidden" in normalized_error:
            raise YouTubeGvsForbiddenError()
        if "http error 429" in normalized_error or "too many requests" in normalized_error:
            raise YouTubeRateLimitedError()
        if re.search(r"http error 5\d\d", normalized_error) or any(
            marker in normalized_error for marker in _NETWORK_TRANSIENT_MARKERS
        ):
            raise YouTubeNetworkTransientError()
        raise YouTubeAcquisitionError()

    @staticmethod
    def _raise_for_provider_diagnostic(
        result: YtDlpCommandResult,
        *,
        video_id: str,
        attempt: int,
    ) -> None:
        if _provider_is_unavailable(result.stderr.lower()):
            logger.warning(
                "youtube_pot_provider_unavailable video_id=%s strategy=%s attempt=%s",
                video_id,
                YOUTUBE_ACQUISITION_STRATEGY,
                attempt,
            )
            raise YouTubePoTokenProviderUnavailableError()


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise YouTubeAcquisitionTimeoutError()
    return remaining


def _directory_size_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _provider_is_unavailable(normalized_error: str) -> bool:
    if any(marker in normalized_error for marker in _PROVIDER_UNAVAILABLE_MARKERS):
        return True
    if "error reaching get " in normalized_error and "/ping" in normalized_error:
        return True
    return "po token provider" in normalized_error and any(
        marker in normalized_error
        for marker in ("not available", "unavailable", "failed", "error")
    )


def _read_bounded_text(file_object, limit_bytes: int) -> str:
    file_object.seek(0)
    output = file_object.read(limit_bytes + 1)
    if len(output) > limit_bytes:
        raise YouTubeAcquisitionError()
    return output.decode("utf-8", errors="replace")


def _select_downloaded_file(
    owned_dir: Path,
    stdout: str,
    *,
    max_file_size_bytes: int,
) -> tuple[Path, str]:
    output_lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    selected_formats = [
        line.removeprefix("SELECTED_FORMAT=")
        for line in output_lines
        if line.startswith("SELECTED_FORMAT=")
    ]
    output_paths = [
        line.removeprefix("OUTPUT=")
        for line in output_lines
        if line.startswith("OUTPUT=")
    ]
    if (
        len(output_lines) != 2
        or len(selected_formats) != 1
        or not selected_formats[0]
        or len(output_paths) != 1
        or not output_paths[0]
    ):
        raise YouTubeAcquisitionError()
    raw_candidate = Path(output_paths[0])
    if raw_candidate.is_symlink():
        raise YouTubeAcquisitionError()
    candidate = raw_candidate.resolve()
    if not candidate.is_relative_to(owned_dir):
        raise YouTubeAcquisitionError()

    files = [
        path.resolve()
        for path in owned_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    if files != [candidate] or not candidate.name.startswith("media."):
        raise YouTubeAcquisitionError()
    if candidate.stat().st_size <= 0:
        raise YouTubeAcquisitionError()
    if candidate.stat().st_size > max_file_size_bytes:
        raise YouTubeSizeLimitExceededError()
    return candidate, selected_formats[0]
