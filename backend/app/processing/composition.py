from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.processing.adapters.celery_dispatcher import CeleryProcessingTaskDispatcher
from app.processing.adapters.legacy_result_sink import SqlAlchemyLegacyProcessingResultSink
from app.processing.adapters.media_source import ObjectStorageProcessingMediaSource
from app.processing.adapters.sqlalchemy_stores import (
    SqlAlchemyDirectUploadArtifactStore,
    SqlAlchemyProcessingArtifactStore,
    SqlAlchemyProcessingRequestRepository,
)
from app.processing.adapters.whisper_transcriber import WhisperProcessingTranscriptionProvider
from app.processing.application.dispatch import DispatchProcessingApplicationService
from app.processing.application.execute import (
    ExecuteDirectUploadProcessingApplicationService,
    ExecuteProcessingApplicationService,
)
from app.services.object_storage import get_object_storage_client


def build_processing_dispatch_service(
    db: Session,
    *,
    dispatcher=None,
) -> DispatchProcessingApplicationService:
    return DispatchProcessingApplicationService(
        repository=SqlAlchemyProcessingRequestRepository(db),
        dispatcher=dispatcher or CeleryProcessingTaskDispatcher(),
    )


def build_processing_execution_service() -> ExecuteProcessingApplicationService:
    db = SessionLocal()
    store = SqlAlchemyProcessingArtifactStore(db)
    return ExecuteProcessingApplicationService(
        media_source=ObjectStorageProcessingMediaSource(get_object_storage_client()),
        transcriber=WhisperProcessingTranscriptionProvider(),
        artifact_store=store,
        result_sink=SqlAlchemyLegacyProcessingResultSink(db),
    )


def build_direct_upload_execution_service() -> ExecuteDirectUploadProcessingApplicationService:
    return ExecuteDirectUploadProcessingApplicationService(
        transcriber=WhisperProcessingTranscriptionProvider(),
        artifact_store=SqlAlchemyDirectUploadArtifactStore(SessionLocal()),
    )
