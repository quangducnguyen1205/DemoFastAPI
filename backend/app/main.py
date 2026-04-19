from fastapi import FastAPI
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # fallback if python-dotenv isn't installed in the local editor
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False
import os
import time
from contextlib import asynccontextmanager

# Load environment variables from .env as early as possible
load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", ".env"), override=False)

from app.routers import videos
from app.core.database import engine, Base
from app.config.settings import settings
# Ensure models are imported so SQLAlchemy registers tables
from app import models as _models  # noqa: F401
from fastapi.middleware.cors import CORSMiddleware

# Create database tables
def create_tables():
    # Wait for a database to be ready
    max_retries = 30
    for i in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            print("Database tables created successfully")
            break
        except Exception as e:
            print(f"Attempt {i+1}/{max_retries} failed: {e}")
            if i < max_retries - 1:
                time.sleep(2)
            else:
                raise

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run initialization and optional teardown.

    Replaces deprecated @app.on_event('startup') / 'shutdown'.
    """
    settings.ensure_media_dirs()
    create_tables()  # startup work
    yield            # application runs
    # (optional) add teardown / cleanup code here

# Create FastAPI app with lifespan handler
app = FastAPI(
    title="AI Knowledge Workspace Processing Service",
    description="Internal processing service for upload, transcription, task tracking, and transcript retrieval.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hoặc ["http://localhost:5173"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(videos.router, prefix="/videos", tags=["videos"])

# Root endpoint
@app.get("/")
def read_root():
    return {
        "message": "AI Knowledge Workspace Processing Service",
        "docs": "/docs",
        "redoc": "/redoc"
    }

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy"}
