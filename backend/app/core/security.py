from __future__ import annotations

import base64
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Dict, Optional

from passlib.context import CryptContext


_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=False)

MAX_PASSWORD_LENGTH = 72  # bcrypt limit
SECRET_KEY = os.getenv("AUTH_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret-key"
ALGORITHM = "HS256"
EXPIRE_MINUTES = 60


def hash_password(plain: str) -> str:
    # Truncate to MAX_PASSWORD_LENGTH to avoid bcrypt errors
    return _pwd_context.hash(plain[:MAX_PASSWORD_LENGTH])


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        # Truncate to match hash_password behavior
        return _pwd_context.verify(plain[:MAX_PASSWORD_LENGTH], hashed)
    except Exception:
        return False


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=EXPIRE_MINUTES))
    to_encode.update({"exp": int(expire.timestamp()), "iat": int(now.timestamp())})

    header = {"alg": ALGORITHM, "typ": "JWT"}
    segments = [
        _b64encode(json.dumps(header, separators=(",", ":")).encode()),
        _b64encode(json.dumps(to_encode, separators=(",", ":"), default=str).encode()),
    ]
    signing_input = ".".join(segments).encode()
    signature = hmac.new(SECRET_KEY.encode(), signing_input, sha256).digest()
    segments.append(_b64encode(signature))
    return ".".join(segments)


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        raise ValueError("Invalid token format")

    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(SECRET_KEY.encode(), signing_input, sha256).digest()
    actual_sig = _b64decode(signature_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid token signature")

    payload = json.loads(_b64decode(payload_b64))
    exp = payload.get("exp")
    if exp is not None:
        if int(exp) < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("Token has expired")
    return payload
