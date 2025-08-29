"""Compatibility re-exports for models and FAISS helpers.

This module used to contain the SQLAlchemy models and FAISS helpers. It now
delegates to the package `app.models` to avoid duplicate table declarations
while keeping imports working (e.g., `from app import models`).
"""

# Re-export everything from the package
from .models import *  # noqa: F401,F403
