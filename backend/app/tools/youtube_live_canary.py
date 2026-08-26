import argparse
import json
from pathlib import Path
import re
import sys
import time

from app.config.settings import settings
from app.processing.adapters.youtube_media_source import (
    SubprocessYtDlpCommandRunner,
    YouTubeProcessingMediaSource,
)
from app.processing.domain.failures import YouTubeAcquisitionError
from app.processing.domain.models import YouTubeProcessingExecutionCommand


_DEFAULT_VIDEO_IDS = ("jNQXAC9IVRw", "zbnv7su3xnk")
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class _ObservingRunner:
    def __init__(self) -> None:
        self._delegate = SubprocessYtDlpCommandRunner()
        self.metadata_success = False

    def run(self, args, **kwargs):
        result = self._delegate.run(args, **kwargs)
        if "--dump-single-json" in args and result.returncode == 0:
            self.metadata_success = True
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LIVE NETWORK TEST - NOT PART OF NORMAL UNIT TESTS",
    )
    parser.add_argument(
        "--live-network-test",
        action="store_true",
        help="required acknowledgement that this command makes real YouTube calls",
    )
    parser.add_argument(
        "--video-id",
        action="append",
        dest="video_ids",
        help="public finite YouTube video ID; may be repeated",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.live_network_test:
        print("Refusing to run without --live-network-test", file=sys.stderr)
        return 2

    video_ids = tuple(args.video_ids or _DEFAULT_VIDEO_IDS)
    if any(not _VIDEO_ID_PATTERN.fullmatch(video_id) for video_id in video_ids):
        print("Every --video-id must be a bare safe YouTube video ID", file=sys.stderr)
        return 2

    print("LIVE NETWORK TEST - NOT PART OF NORMAL UNIT TESTS", flush=True)
    failed = False
    for video_id in video_ids:
        runner = _ObservingRunner()
        source = YouTubeProcessingMediaSource(
            max_duration_seconds=settings.YOUTUBE_MAX_DURATION_SECONDS,
            max_file_size_bytes=settings.YOUTUBE_MAX_FILE_SIZE_BYTES,
            socket_timeout_seconds=settings.YOUTUBE_SOCKET_TIMEOUT_SECONDS,
            acquisition_timeout_seconds=settings.YOUTUBE_ACQUISITION_TIMEOUT_SECONDS,
            download_retries=settings.YOUTUBE_DOWNLOAD_RETRIES,
            po_token_provider_url=settings.YOUTUBE_PO_TOKEN_PROVIDER_URL,
            runner=runner,
        )
        command = YouTubeProcessingExecutionCommand(
            event_id=f"youtube-live-canary-{video_id}",
            asset_id=f"youtube-live-canary-{video_id}",
            workspace_id="youtube-live-canary",
            owner_id="youtube-live-canary",
            youtube_video_id=video_id,
        )
        started = time.monotonic()
        acquired_path: Path | None = None
        record = {
            "video_id": video_id,
            "metadata_success": False,
            "media_bytes_success": False,
            "bytes_read": 0,
            "elapsed_seconds": None,
            "temporary_media_cleaned": False,
            "safe_error_family": None,
        }
        try:
            with source.acquire(command) as media_path:
                acquired_path = Path(media_path)
                record["media_bytes_success"] = acquired_path.stat().st_size > 0
                record["bytes_read"] = acquired_path.stat().st_size
        except YouTubeAcquisitionError as exc:
            record["safe_error_family"] = exc.diagnostic_family
            failed = True
        except Exception:
            record["safe_error_family"] = "youtube_canary_internal_error"
            failed = True
        record["metadata_success"] = runner.metadata_success
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["temporary_media_cleaned"] = (
            acquired_path is None or not acquired_path.exists()
        )
        if not record["media_bytes_success"] or not record["temporary_media_cleaned"]:
            failed = True
        print(json.dumps(record, sort_keys=True), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
