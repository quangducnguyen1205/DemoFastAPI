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
    # No processing attempt may hold a worker slot indefinitely. The soft limit raises
    # SoftTimeLimitExceeded inside the running task, which the processing use case handles like any
    # other failure: it persists the failure, publishes one result and releases the lease. The hard
    # limit is the net for work that cannot be interrupted in Python; it kills the worker child,
    # which Celery replaces, so capacity returns on its own.
    task_soft_time_limit=settings.PROCESSING_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=settings.PROCESSING_HARD_TIME_LIMIT_SECONDS,
    broker_transport_options={
        "visibility_timeout": settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
    },
    result_backend_transport_options={
        "visibility_timeout": settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
    },
    visibility_timeout=settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
)


@worker_process_init.connect
def initialize_worker_database_schema(**_kwargs) -> None:
    initialize_database_schema()
