import logging
import signal

from app.config.settings import settings
from app.core.schema import initialize_database_schema
from app.processing.adapters.celery_dispatcher import CeleryProcessingTaskDispatcher
from app.processing.adapters.sqlalchemy_stores import SqlAlchemyProcessingRequestRepository
from app.processing.application.dispatch import DispatchProcessingApplicationService


def build_processing_dispatch_service(db, *, dispatcher=None) -> DispatchProcessingApplicationService:
    return DispatchProcessingApplicationService(
        repository=SqlAlchemyProcessingRequestRepository(db),
        dispatcher=dispatcher or CeleryProcessingTaskDispatcher(),
    )


def run_processing_consumer() -> None:
    from app.consumers.asset_processing_consumer import AssetProcessingKafkaConsumer

    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    initialize_database_schema()
    runner = AssetProcessingKafkaConsumer()
    signal.signal(signal.SIGTERM, runner.stop)
    signal.signal(signal.SIGINT, runner.stop)
    runner.run_forever()


__all__ = ["build_processing_dispatch_service", "run_processing_consumer"]
