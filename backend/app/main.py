from fastapi import FastAPI
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # fallback if python-dotenv isn't installed in local editor
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False
import os
import time
from contextlib import asynccontextmanager

# Load environment variables from .env as early as possible
load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", ".env"), override=False)

from .routers import users, videos
from .core.database import engine, Base
# Ensure models are imported so SQLAlchemy registers tables
from . import models as _models  # noqa: F401

# Create database tables
def create_tables():
    # Wait for database to be ready
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
    create_tables()  # startup work
    yield            # application runs
    # (optional) add teardown / cleanup code here

# Create FastAPI app with lifespan handler
app = FastAPI(
    title="User Management API",
    description="A FastAPI application for managing users with PostgreSQL",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Include routers
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(videos.router, prefix="/videos", tags=["videos"])

# Root endpoint
@app.get("/")
def read_root():
    return {
        "message": "Welcome to User Management API",
        "docs": "/docs",
        "redoc": "/redoc"
    }

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy"}
