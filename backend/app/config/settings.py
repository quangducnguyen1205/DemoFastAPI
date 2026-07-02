import os
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False


# Load from .env for local runs; docker-compose also injects env
load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", ".env"), override=False)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name)
    return val if (val is not None and val != "") else default


def _env_int(name: str, default: int) -> int:
    val = _env(name)
    if val is None:
        return default
    return int(val)


def _env_positive_int(name: str, default: int) -> int:
    val = _env(name)
    if val is None:
        value = default
    else:
        try:
            value = int(val)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value


def _env_float(name: str, default: float) -> float:
    val = _env(name)
    if val is None:
        return default
    return float(val)


def _env_bool(name: str, default: bool) -> bool:
    val = _env(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def _is_docker() -> bool:
    # Heuristic: docker sets this file
    return Path("/.docker").exists() or _env("DOCKERIZED", "false").lower() in {"1", "true", "yes"}


class Settings:
    # Core
    DATABASE_URL: str = _env("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/userdb")  # local fallback

    # Media configuration
    # If running in docker, default to /backend/media; else default to ./media for local runs
    MEDIA_ROOT: str = _env("MEDIA_ROOT", "/backend/media" if _is_docker() else "media")
    VIDEO_SUBDIR: str = _env("VIDEO_SUBDIR", "videos")

    # Celery / Redis
    CELERY_BROKER_URL: str = _env("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND: str = _env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = _env_int("CELERY_WORKER_PREFETCH_MULTIPLIER", 1)

    # Kafka consumer configuration. The broker itself is owned outside this repo.
    KAFKA_BOOTSTRAP_SERVERS: str = _env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    KAFKA_ASSET_PROCESSING_TOPIC: str = _env("KAFKA_ASSET_PROCESSING_TOPIC", "asset.processing.requested.v1")
    KAFKA_CONSUMER_GROUP: str = _env("KAFKA_CONSUMER_GROUP", "fastapi-processing-v1")
    KAFKA_AUTO_OFFSET_RESET: str = _env("KAFKA_AUTO_OFFSET_RESET", "earliest")
    KAFKA_RECONNECT_BACKOFF_SECONDS: int = _env_int("KAFKA_RECONNECT_BACKOFF_SECONDS", 5)
    KAFKA_PROCESSING_RESULT_TOPIC: str = _env("KAFKA_PROCESSING_RESULT_TOPIC", "asset.processing.result.v1")
    KAFKA_SEND_TIMEOUT_SECONDS: float = _env_float("KAFKA_SEND_TIMEOUT_SECONDS", 10.0)

    # Result outbox relay configuration. The relay is intentionally manual/off by default.
    PROCESSING_RESULT_PUBLISHER_ENABLED: bool = _env_bool("PROCESSING_RESULT_PUBLISHER_ENABLED", False)
    PROCESSING_OUTBOX_RELAY_ENABLED: bool = _env_bool("PROCESSING_OUTBOX_RELAY_ENABLED", False)
    PROCESSING_OUTBOX_RELAY_BATCH_SIZE: int = _env_int("PROCESSING_OUTBOX_RELAY_BATCH_SIZE", 10)
    PROCESSING_OUTBOX_RELAY_MAX_ATTEMPTS: int = _env_int("PROCESSING_OUTBOX_RELAY_MAX_ATTEMPTS", 5)
    PROCESSING_OUTBOX_RELAY_RETRY_DELAY_SECONDS: int = _env_int(
        "PROCESSING_OUTBOX_RELAY_RETRY_DELAY_SECONDS",
        60,
    )
    PROCESSING_OUTBOX_AUTO_RELAY_ENABLED: bool = _env_bool("PROCESSING_OUTBOX_AUTO_RELAY_ENABLED", False)
    PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS: int = _env_positive_int(
        "PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS",
        10,
    )
    PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE: int = _env_positive_int(
        "PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE",
        10,
    )

    # S3-compatible object storage for Spring-owned media references.
    OBJECT_STORAGE_ENDPOINT_URL: str = _env(
        "OBJECT_STORAGE_ENDPOINT_URL",
        _env("MINIO_ENDPOINT_URL", "http://localhost:9000"),
    )
    OBJECT_STORAGE_ACCESS_KEY_ID: str = _env(
        "OBJECT_STORAGE_ACCESS_KEY_ID",
        _env("MINIO_ACCESS_KEY", "minioadmin"),
    )
    OBJECT_STORAGE_SECRET_ACCESS_KEY: str = _env(
        "OBJECT_STORAGE_SECRET_ACCESS_KEY",
        _env("MINIO_SECRET_KEY", "minioadmin"),
    )
    OBJECT_STORAGE_REGION: str = _env("OBJECT_STORAGE_REGION", "us-east-1")
    LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

    # Internal assistant generation. Disabled by default; Spring supplies all context.
    ASSISTANT_LLM_ENABLED: bool = _env_bool("ASSISTANT_LLM_ENABLED", False)
    ASSISTANT_OLLAMA_BASE_URL: str = _env("ASSISTANT_OLLAMA_BASE_URL", "")
    ASSISTANT_OLLAMA_MODEL: str = _env("ASSISTANT_OLLAMA_MODEL", "")
    ASSISTANT_OLLAMA_TIMEOUT_SECONDS: float = _env_float("ASSISTANT_OLLAMA_TIMEOUT_SECONDS", 15.0)

    @property
    def KAFKA_BOOTSTRAP_SERVERS_LIST(self) -> list[str]:
        return [server.strip() for server in self.KAFKA_BOOTSTRAP_SERVERS.split(",") if server.strip()]

    @property
    def VIDEO_DIR(self) -> str:
        return str(Path(self.MEDIA_ROOT) / self.VIDEO_SUBDIR)

    def ensure_media_dirs(self) -> None:
        Path(self.VIDEO_DIR).mkdir(parents=True, exist_ok=True)


settings = Settings()
