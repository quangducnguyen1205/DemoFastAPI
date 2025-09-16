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


def _is_docker() -> bool:
    # Heuristic: docker sets this file
    return Path("/.docker").exists() or _env("DOCKERIZED", "false").lower() in {"1", "true", "yes"}


class Settings:
    # Core
    DATABASE_URL: str = _env("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/userdb")  # local fallback

    # Media configuration
    # If running in docker, default to /app/media; else default to ./media for local runs
    MEDIA_ROOT: str = _env("MEDIA_ROOT", "/backend/media" if _is_docker() else "media")
    VIDEO_SUBDIR: str = _env("VIDEO_SUBDIR", "videos")

    # FAISS files
    FAISS_INDEX_PATH: str = _env("FAISS_INDEX_PATH") or str(Path(MEDIA_ROOT) / "faiss_index.faiss")
    FAISS_MAPPING_PATH: str = _env("FAISS_MAPPING_PATH") or str(Path(MEDIA_ROOT) / "faiss_mapping.pkl")

    # Celery / Redis
    CELERY_BROKER_URL: str = _env("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND: str = _env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

    # Feature flags / misc
    TOKENIZERS_PARALLELISM: str = _env("TOKENIZERS_PARALLELISM", "false")

    @property
    def VIDEO_DIR(self) -> str:
        return str(Path(self.MEDIA_ROOT) / self.VIDEO_SUBDIR)


settings = Settings()
