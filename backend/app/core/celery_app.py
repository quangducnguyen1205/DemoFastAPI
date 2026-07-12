from celery import Celery
from celery.signals import worker_process_init
from app.config.settings import settings
from app.core.schema import initialize_database_schema


celery_app = Celery(
    "backend_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.video_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
)


@worker_process_init.connect
def initialize_worker_database_schema(**_kwargs) -> None:
    initialize_database_schema()
