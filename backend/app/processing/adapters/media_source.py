from contextlib import contextmanager
import tempfile
import time

from app.processing.domain.models import ProcessingExecutionCommand
from app.services.object_storage import ObjectStorageClient
from app.processing.adapters.timing import log_processing_timing


class ObjectStorageProcessingMediaSource:
    def __init__(self, client: ObjectStorageClient) -> None:
        self._client = client

    @contextmanager
    def acquire(self, command: ProcessingExecutionCommand):
        with tempfile.TemporaryDirectory(prefix="asset_") as temp_dir:
            started_at = time.perf_counter()
            media_path = self._client.download_to_file(
                bucket=command.storage_bucket,
                object_key=command.object_key,
                destination_dir=temp_dir,
                filename=command.original_filename,
            )
            log_processing_timing(
                "object_download_ms",
                (time.perf_counter() - started_at) * 1000,
                asset_id=command.asset_id,
                bucket=command.storage_bucket,
                object_key=command.object_key,
            )
            yield media_path
