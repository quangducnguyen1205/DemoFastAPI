"""Compatibility shim for code importing from app.schemas directly.

Re-exports the new, split schema modules so existing imports keep working.
"""

from .schemas import (  # type: ignore[F401]
    UserCreate,
    UserUpdate,
    UserRead,
    VideoBase,
    VideoCreate,
    VideoRead,
    VideoUploadResponse,
    VideoSearchResult,
    TranscriptBase,
    TranscriptCreate,
    TranscriptRead,
)

