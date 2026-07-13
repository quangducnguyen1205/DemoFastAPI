from sqlalchemy.orm import Session

from app import models
from app.processing.domain.models import ProcessingFailed, ProcessingOutcome, ProcessingSucceeded
from app.services.processing_outbox import add_processing_failed_event, add_transcript_ready_event


class SqlAlchemyLegacyProcessingResultSink:
    """Temporary adapter retained until result delivery moves behind its own feature boundary."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def record(self, outcome: ProcessingOutcome) -> None:
        request = self._db.query(models.ProcessingRequest).filter(
            models.ProcessingRequest.event_id == outcome.event_id,
        ).one()
        if isinstance(outcome, ProcessingSucceeded):
            add_transcript_ready_event(
                self._db,
                processing_request=request,
                segment_count=outcome.artifact.segment_count,
                completed_at=outcome.completed_at,
            )
        elif isinstance(outcome, ProcessingFailed):
            add_processing_failed_event(
                self._db,
                processing_request=request,
                exc=outcome.failure.cause,
                completed_at=outcome.completed_at,
            )
