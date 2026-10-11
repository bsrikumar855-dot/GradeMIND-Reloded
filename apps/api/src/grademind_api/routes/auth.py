from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_api.deps import client_ip, db_dep, principal_any_dep, settings_dep
from grademind_api.errors import ApiError
from grademind_core.config import Settings
from grademind_core.db.models import AuditLog, User
from grademind_core.login_throttle import Limits, record, retry_after
from grademind_core.passwords import MAX_LENGTH, check_new_password
from grademind_core.security import Principal, create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int
    must_change_password: bool = False


class MeOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    display_name: str
    role: str
    must_change_password: bool = False


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
    tok = create_access_token(settings, Principal(user.id, user.org_id, user.role, user.token_version))
    return TokenOut(access_token=tok, expires_in=settings.jwt_ttl_seconds, must_change_password=user.must_change_password)


@router.get("/me", response_model=MeOut)
def me(p: Principal = Depends(principal_any_dep), db: Session = Depends(db_dep)) -> MeOut:
    u = db.get(User, p.user_id)
    assert u is not None  # the principal dependency already verified the user
    return MeOut(
        id=u.id,
        org_id=u.org_id,
        email=u.email,
        display_name=u.display_name,
        role=u.role.value,
        must_change_password=u.must_change_password,
    )


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_LENGTH)
    new_password: str = Field(min_length=1, max_length=MAX_LENGTH)


@router.post("/auth/change-password", response_model=TokenOut)
def change_password(
    body: ChangePasswordIn,
    request: Request,
    p: Principal = Depends(principal_any_dep),
    db: Session = Depends(db_dep),
    settings: Settings = Depends(settings_dep),
) -> TokenOut:
    """Self-service change. The current password is required and wrong guesses are throttled like sign-in attempts. On success
    every OTHER session ends (token_version), a fresh token for this one is returned, and the audit log records that it
    happened (never the passwords)."""
    u = db.get(User, p.user_id)
    assert u is not None
    ip = client_ip(request, settings)
    limits = Limits.from_settings(settings)
    blocked = retry_after(db, u.email, ip, limits)
    if blocked is not None:
        raise ApiError(
            429,
            "too_many_attempts",
            "Too many attempts. Wait a few minutes and try again.",
            headers={"Retry-After": str(blocked[0])},
        )
    if not verify_password(u.password_hash, body.current_password):
        reached = record(db, u.email, ip, False, limits)
        if reached is not None:
            db.add(
                AuditLog(
                    actor_id=u.id,
                    action="auth.password_change_throttled",
                    entity_type="user",
                    entity_id=str(u.id),
                    details={"limit": reached},
                )
            )
        db.commit()
        raise ApiError(403, "invalid_current_password", "The current password is not correct.")
    why = check_new_password(body.new_password, body.current_password, u.email)
    if why is not None:
        raise ApiError(422, "weak_password", why)
    u.password_hash = hash_password(body.new_password)
    u.token_version += 1
    forced = u.must_change_password
    u.must_change_password = False
    u.password_changed_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor_id=u.id,
            action="auth.password_changed",
            entity_type="user",
            entity_id=str(u.id),
            request_id=request.state.request_id,
            details={"was_forced": forced},
        )
    )
    db.commit()
    tok = create_access_token(settings, Principal(u.id, u.org_id, u.role, u.token_version))
    return TokenOut(access_token=tok, expires_in=settings.jwt_ttl_seconds, must_change_password=False)
