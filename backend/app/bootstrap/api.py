import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.bootstrap.assistant import assistant_router
from app.config.settings import settings
from app.core.internal_auth import require_internal_access
from app.core.schema import initialize_database_schema
from app.routers import internal_processing, videos


def initialize_api_database() -> None:
    max_retries = 30
    for attempt in range(max_retries):
        try:
            initialize_database_schema()
            print("Database tables created successfully")
            return
        except Exception as exc:
            print(f"Attempt {attempt + 1}/{max_retries} failed: {exc}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise


@asynccontextmanager
async def api_lifespan(_app: FastAPI):
    settings.ensure_media_dirs()
    initialize_api_database()
    yield


def create_api_app() -> FastAPI:
    if settings.INTERNAL_API_AUTH_ENABLED and not settings.INTERNAL_API_TOKEN:
        raise RuntimeError(
            "INTERNAL_API_AUTH_ENABLED requires INTERNAL_API_TOKEN to be configured"
        )

    app = FastAPI(
        title="AI Knowledge Workspace Processing Service",
        description="Internal processing service for upload, transcription, task tracking, and transcript retrieval.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=api_lifespan,
    )

    # This service has no browser callers; the public product boundary is the Spring core.
    # It therefore configures no CORS, and every application router shares one guard.
    internal_guard = [Depends(require_internal_access)]
    app.include_router(videos.router, prefix="/videos", tags=["videos"], dependencies=internal_guard)
    app.include_router(assistant_router(), dependencies=internal_guard)
    app.include_router(internal_processing.router, dependencies=internal_guard)

    @app.get("/")
    def read_root():
        return {
            "message": "AI Knowledge Workspace Processing Service",
            "docs": "/docs",
            "redoc": "/redoc",
        }

    @app.get("/health")
    def health_check():
        return {"status": "healthy"}

    return app
