"""Request-scoped dependencies: settings, DB session, authenticated principal, role guard (spec §16 RBAC)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from grademind_api.errors import ApiError
from grademind_core.config import Settings
from grademind_core.db.models import Role, User
from grademind_core.security import Principal, decode_access_token

_bearer = HTTPBearer(auto_error=False)


def settings_dep(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def db_dep(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as s:
        yield s


def principal_dep(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(settings_dep),
    db: Session = Depends(db_dep),
) -> Principal:
    if creds is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    try:
        p = decode_access_token(settings, creds.credentials)
    except jwt.PyJWTError as e:
        raise ApiError(401, "invalid_token", "Your session has expired. Sign in again.") from e
    user = db.get(User, p.user_id)
    if user is None or not user.is_active or user.org_id != p.org_id or user.role != p.role:
        raise ApiError(401, "invalid_token", "Your session is no longer valid. Sign in again.")
    return p


def require_roles(*roles: Role) -> Callable[[Principal], Principal]:
    def _guard(p: Principal = Depends(principal_dep)) -> Principal:
        if p.role not in roles:
            raise ApiError(403, "forbidden", "You do not have permission to do this.")
        return p

    return _guard
