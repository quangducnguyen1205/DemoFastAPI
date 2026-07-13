import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap.assistant import assistant_router
from app.config.settings import settings
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
    app = FastAPI(
        title="AI Knowledge Workspace Processing Service",
        description="Internal processing service for upload, transcription, task tracking, and transcript retrieval.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=api_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(videos.router, prefix="/videos", tags=["videos"])
    app.include_router(assistant_router())
    app.include_router(internal_processing.router)

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
