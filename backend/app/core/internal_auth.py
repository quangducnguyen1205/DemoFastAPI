"""Shared-credential guard for the internal service HTTP boundary."""

import hmac
import logging

from fastapi import HTTPException, Request

from app.config.settings import settings

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "bearer "


def require_internal_access(request: Request) -> None:
    if not settings.INTERNAL_API_AUTH_ENABLED:
        return

    expected = settings.INTERNAL_API_TOKEN
    authorization = request.headers.get("authorization") or ""
    if not expected or not authorization.lower().startswith(_BEARER_PREFIX):
        logger.info(
            "rejecting internal API request without a bearer credential path=%s",
            request.url.path,
        )
        raise HTTPException(
            status_code=401,
            detail="Internal service credential required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided = authorization[len(_BEARER_PREFIX):].strip()
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        logger.info(
            "rejecting internal API request with an invalid bearer credential path=%s",
            request.url.path,
        )
        raise HTTPException(status_code=403, detail="Invalid internal service credential")
