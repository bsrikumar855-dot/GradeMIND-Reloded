from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, settings_dep
from grademind_api.errors import ApiError
from grademind_core.config import Settings
from grademind_core.db.models import AuditLog, User
from grademind_core.security import Principal, create_access_token, verify_password

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


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(db_dep), settings: Settings = Depends(settings_dep)) -> TokenOut:
    user = db.scalars(select(User).where(User.email == body.email.lower())).first()
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect.")  # same message either way
    db.add(AuditLog(actor_id=user.id, action="auth.login", entity_type="user", entity_id=str(user.id)))
    db.commit()
    tok = create_access_token(settings, Principal(user.id, user.org_id, user.role))
    return TokenOut(access_token=tok, expires_in=settings.jwt_ttl_seconds)


@router.get("/me", response_model=MeOut)
def me(p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> MeOut:
    u = db.get(User, p.user_id)
    assert u is not None  # principal_dep already verified the user
    return MeOut(id=u.id, org_id=u.org_id, email=u.email, display_name=u.display_name, role=u.role.value)
