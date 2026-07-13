import logging

logger = logging.getLogger(__name__)


def log_processing_timing(
    metric: str,
    value_ms: float,
    *,
    task_id: str | None = None,
    video_id: int | None = None,
    asset_id: str | None = None,
    **extra,
) -> None:
    parts = [
        f"{metric}={value_ms:.2f}",
        f"task_id={task_id}",
        f"video_id={video_id}",
        f"asset_id={asset_id}",
    ]
    parts.extend(f"{key}={value}" for key, value in extra.items() if value is not None)
    logger.info(" ".join(parts))
