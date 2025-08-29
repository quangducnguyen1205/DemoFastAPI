"""Compatibility shim that re-exports Celery tasks from the tasks package.

This allows existing imports like `from app.tasks import process_video_task`
to continue working while the implementation lives in `app.tasks.video_tasks`.
"""

from .tasks.video_tasks import process_video_task  # noqa: F401
