"""Password hashing (argon2id) and short-lived JWT access tokens. Secrets come only from the single config source."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from grademind_core.config import Settings
from grademind_core.db.models import Role

_hasher = PasswordHasher()
_ALG = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role


def create_access_token(settings: Settings, p: Principal) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(p.user_id),
        "org": str(p.org_id),
        "role": p.role.value,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=_ALG)


def decode_access_token(settings: Settings, token: str) -> Principal:
    """Raises jwt.PyJWTError on any invalid, expired or tampered token."""
    c = jwt.decode(
        token, settings.jwt_secret.get_secret_value(), algorithms=[_ALG], options={"require": ["sub", "exp", "role", "org"]}
    )
    return Principal(user_id=uuid.UUID(c["sub"]), org_id=uuid.UUID(c["org"]), role=Role(c["role"]))
