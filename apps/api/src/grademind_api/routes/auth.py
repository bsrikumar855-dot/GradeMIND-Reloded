from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_api.deps import client_ip, db_dep, principal_dep, settings_dep
from grademind_api.errors import ApiError
from grademind_core.config import Settings
from grademind_core.db.models import AuditLog, User
from grademind_core.login_throttle import Limits, record, retry_after
from grademind_core.security import Principal, create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int


class MeOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    display_name: str
    role: str


_DUMMY_HASH = hash_password(
    "not-the-password-of-anyone"
)  # verified when the account does not exist, so a miss costs the same time as a hit


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(db_dep), settings: Settings = Depends(settings_dep)) -> TokenOut:
    email = body.email.lower()
    ip = client_ip(request, settings)
    limits = Limits.from_settings(settings)
    blocked = retry_after(db, email, ip, limits)
    if blocked is not None:
        wait, _which = blocked
        raise ApiError(
            429,
            "too_many_attempts",
            "Too many sign-in attempts. Wait a few minutes and try again.",
            headers={"Retry-After": str(wait)},
        )
    user = db.scalars(select(User).where(User.email == email)).first()
    # always check a password hash, a real one or a dummy, so the time taken does not reveal whether the account exists
    password_ok = verify_password(user.password_hash if user else _DUMMY_HASH, body.password)
    if user is None or not user.is_active or not password_ok:
        reached = record(db, email, ip, False, limits)
        if reached is not None:
            db.add(AuditLog(actor_id=None, action="auth.login_throttled", entity_type="login", details={"limit": reached}))
        db.commit()
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect.")  # same message either way
    record(db, email, ip, True, limits)
    db.add(AuditLog(actor_id=user.id, action="auth.login", entity_type="user", entity_id=str(user.id)))
    db.commit()
    tok = create_access_token(settings, Principal(user.id, user.org_id, user.role))
    return TokenOut(access_token=tok, expires_in=settings.jwt_ttl_seconds)


@router.get("/me", response_model=MeOut)
def me(p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> MeOut:
    u = db.get(User, p.user_id)
    assert u is not None  # principal_dep already verified the user
    return MeOut(id=u.id, org_id=u.org_id, email=u.email, display_name=u.display_name, role=u.role.value)
