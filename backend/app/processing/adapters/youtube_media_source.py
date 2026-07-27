from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Protocol

from app.processing.domain.failures import (
    YouTubeAcquisitionError,
    YouTubeAcquisitionTimeoutError,
    YouTubeDurationLimitExceededError,
    YouTubeLiveNotSupportedError,
    YouTubeSizeLimitExceededError,
    YouTubeUnavailableError,
)
from app.processing.domain.models import YouTubeProcessingExecutionCommand


YOUTUBE_CANONICAL_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
_METADATA_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
_COMMAND_OUTPUT_LIMIT_BYTES = 64 * 1024
_UNAVAILABLE_MARKERS = (
    "private video",
    "video unavailable",
    "video has been removed",
    "not available in your country",
    "geo restricted",
    "age-restricted",
    "sign in to confirm your age",
    "members-only",
    "copyright",
)


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
        runner: YtDlpCommandRunner | None = None,
    ) -> None:
        self._max_duration_seconds = max_duration_seconds
        self._max_file_size_bytes = max_file_size_bytes
        self._socket_timeout_seconds = socket_timeout_seconds
        self._acquisition_timeout_seconds = acquisition_timeout_seconds
        self._download_retries = download_retries
        self._runner = runner or SubprocessYtDlpCommandRunner()

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
            metadata = self._read_metadata(
                command,
                canonical_url=canonical_url,
                owned_dir=owned_dir,
                deadline=deadline,
            )
            self._validate_metadata(command, metadata)
            media_path = self._download(
                canonical_url=canonical_url,
                owned_dir=owned_dir,
                deadline=deadline,
            )
            yield str(media_path)

    def _base_args(self) -> list[str]:
        retries = str(self._download_retries)
        return [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-plugin-dirs",
            "--no-remote-components",
            "--no-cache-dir",
            "--no-cookies",
            "--no-cookies-from-browser",
            "--no-playlist",
            "--abort-on-error",
            "--no-wait-for-video",
            "--no-progress",
            "--no-warnings",
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
        ]

    def _read_metadata(
        self,
        command: YouTubeProcessingExecutionCommand,
        *,
        canonical_url: str,
        owned_dir: Path,
        deadline: float,
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
        canonical_url: str,
        owned_dir: Path,
        deadline: float,
    ) -> Path:
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
                "after_move:filepath",
                "--no-simulate",
                "--",
                canonical_url,
            ],
            timeout_seconds=_remaining_timeout(deadline),
            cwd=owned_dir,
            max_directory_size=self._max_file_size_bytes,
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
        raise YouTubeAcquisitionError()


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
) -> Path:
    output_lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(output_lines) != 1:
        raise YouTubeAcquisitionError()
    raw_candidate = Path(output_lines[0])
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
    return candidate
