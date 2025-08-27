import os
from celery import Celery
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False


def _env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    return val if (val is not None and val != "") else default


# Load environment for local development
load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", ".env"), override=False)

CELERY_BROKER_URL = _env("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = _env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

celery_app = Celery(
    "backend_tasks",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["app.tasks"],
)

# Reasonable defaults
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
