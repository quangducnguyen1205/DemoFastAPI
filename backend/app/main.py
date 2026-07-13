"""Stable ASGI import path composed by the API bootstrap."""

from app.bootstrap.api import create_api_app

app = create_api_app()
