from app.config.settings import settings
from app.core.database import SessionLocal
from app.processing.adapters.media_source import ObjectStorageProcessingMediaSource
from app.processing.adapters.youtube_media_source import YouTubeProcessingMediaSource
from app.processing.adapters.sqlalchemy_stores import (
    SqlAlchemyDirectUploadArtifactStore,
    SqlAlchemyProcessingArtifactStore,
)
from app.processing.adapters.whisper_transcriber import WhisperProcessingTranscriptionProvider
from app.processing.application.execute import (
    ExecuteDirectUploadProcessingApplicationService,
    ExecuteProcessingApplicationService,
)
from app.result_delivery.adapters.sqlalchemy_repository import SqlAlchemyProcessingResultOutboxRepository
from app.result_delivery.application.record_result import RecordProcessingResultApplicationService
from app.services.object_storage import get_object_storage_client


def build_processing_execution_service() -> ExecuteProcessingApplicationService:
    db = SessionLocal()
    store = SqlAlchemyProcessingArtifactStore(
        db,
        lease_seconds=settings.PROCESSING_LEASE_SECONDS,
    )
    return ExecuteProcessingApplicationService(
        media_source=ObjectStorageProcessingMediaSource(get_object_storage_client()),
        transcriber=WhisperProcessingTranscriptionProvider(),
        artifact_store=store,
        result_sink=RecordProcessingResultApplicationService(
            SqlAlchemyProcessingResultOutboxRepository(db)
        ),
    )


def build_youtube_processing_execution_service() -> ExecuteProcessingApplicationService:
    db = SessionLocal()
    store = SqlAlchemyProcessingArtifactStore(
        db,
        lease_seconds=settings.PROCESSING_LEASE_SECONDS,
    )
    return ExecuteProcessingApplicationService(
        media_source=YouTubeProcessingMediaSource(
            max_duration_seconds=settings.YOUTUBE_MAX_DURATION_SECONDS,
            max_file_size_bytes=settings.YOUTUBE_MAX_FILE_SIZE_BYTES,
            socket_timeout_seconds=settings.YOUTUBE_SOCKET_TIMEOUT_SECONDS,
            acquisition_timeout_seconds=settings.YOUTUBE_ACQUISITION_TIMEOUT_SECONDS,
            download_retries=settings.YOUTUBE_DOWNLOAD_RETRIES,
        ),
        transcriber=WhisperProcessingTranscriptionProvider(),
        artifact_store=store,
        result_sink=RecordProcessingResultApplicationService(
            SqlAlchemyProcessingResultOutboxRepository(db)
        ),
    )


def build_direct_upload_execution_service() -> ExecuteDirectUploadProcessingApplicationService:
    return ExecuteDirectUploadProcessingApplicationService(
        transcriber=WhisperProcessingTranscriptionProvider(),
        artifact_store=SqlAlchemyDirectUploadArtifactStore(SessionLocal()),
    )
