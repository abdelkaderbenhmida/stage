"""Password hashing, JWT issuance, and token primitives.

- Passwords hashed with Argon2id.
- Access tokens are short-lived JWTs (30 min default); refresh tokens are
  opaque random strings stored server-side so they can be revoked.
"""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from controlplane.core.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, Exception):  # noqa: BLE001 - never leak hash validity
        return False


def _create_token(subject: str, token_type: str, lifetime_minutes: int, extra: dict | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=lifetime_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, role: str = "user") -> str:
    return _create_token(user_id, "access", settings.access_token_minutes, {"role": role})


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Store only a digest of the refresh token server-side."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def random_secret() -> str:
    return secrets.token_urlsafe(64)


def random_hex(bytes_count: int = 32) -> str:
    return secrets.token_hex(bytes_count)


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
