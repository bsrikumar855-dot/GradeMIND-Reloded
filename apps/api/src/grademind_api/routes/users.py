"""User administration (4.1, D29). Admin only, own organisation only. Every change is audited with ids and roles, never a
password.

Before this, users were created with the CLI and examiners were assigned by API only. The password is set by the administrator
(a temporary one they pass on; there is no e-mail in a local-only deployment). It is stored hashed and never returned or logged.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, require_roles
from grademind_api.errors import ApiError
from grademind_api.routes.exams import visible_exam
from grademind_core.db.models import AuditLog, ExamAssignment, Role, User
from grademind_core.security import Principal, hash_password

router = APIRouter(tags=["users"])
MIN_PASSWORD = 12  # the same floor the CLI enforces


class UserIn(BaseModel):
    email: EmailStr
    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    role: Role
    password: Annotated[str, Field(min_length=MIN_PASSWORD, max_length=200)]


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    is_active: bool
    created_at: datetime


def _out(u: User) -> UserOut:
    return UserOut(
        id=u.id, email=u.email, display_name=u.display_name, role=u.role, is_active=u.is_active, created_at=u.created_at
    )


def _audit(db: Session, p: Principal, request: Request, action: str, entity_type: str, entity_id: str, **details: object) -> None:
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request.state.request_id,
            details=details,
        )
    )


def _org_user(db: Session, p: Principal, user_id: uuid.UUID) -> User:
    u = db.get(User, user_id)
    if u is None or u.org_id != p.org_id:
        raise ApiError(404, "not_found", "User not found.")  # another organisation's users are not revealed
    return u


@router.get("/users", response_model=list[UserOut])
def list_users(p: Principal = Depends(require_roles(Role.ADMIN)), db: Session = Depends(db_dep)) -> list[UserOut]:
    return [_out(u) for u in db.scalars(select(User).where(User.org_id == p.org_id).order_by(User.created_at, User.id))]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserIn, request: Request, p: Principal = Depends(require_roles(Role.ADMIN)), db: Session = Depends(db_dep)
) -> UserOut:
    u = User(
        org_id=p.org_id,
        email=body.email,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(u)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise ApiError(409, "email_in_use", "A user with this email address already exists.") from e
    _audit(db, p, request, "user.create", "user", str(u.id), role=u.role.value)
    db.commit()
    return _out(u)


def _set_active(db: Session, p: Principal, request: Request, user_id: uuid.UUID, active: bool) -> UserOut:
    u = _org_user(db, p, user_id)
    if u.id == p.user_id:
        raise ApiError(409, "own_account", "You cannot deactivate your own account.")
    if u.is_active != active:
        u.is_active = active  # takes effect on the user's next request: every request re-checks the account
        _audit(db, p, request, "user.activate" if active else "user.deactivate", "user", str(u.id), role=u.role.value)
        db.commit()
    return _out(u)


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(
    user_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(Role.ADMIN)), db: Session = Depends(db_dep)
) -> UserOut:
    return _set_active(db, p, request, user_id, False)


@router.post("/users/{user_id}/activate", response_model=UserOut)
def activate_user(
    user_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(Role.ADMIN)), db: Session = Depends(db_dep)
) -> UserOut:
    return _set_active(db, p, request, user_id, True)


# ---------------------------------------------------------------------------------------------- examiners of an exam


@router.get("/exams/{exam_id}/assignments", response_model=list[UserOut])
def list_assignments(
    exam_id: uuid.UUID, p: Principal = Depends(require_roles(Role.ADMIN)), db: Session = Depends(db_dep)
) -> list[UserOut]:
    visible_exam(db, p, exam_id)
    q = (
        select(User)
        .join(ExamAssignment, ExamAssignment.user_id == User.id)
        .where(ExamAssignment.exam_id == exam_id, User.org_id == p.org_id)
        .order_by(User.display_name, User.id)
    )
    return [_out(u) for u in db.scalars(q)]


@router.delete("/exams/{exam_id}/assignments/{user_id}", status_code=204)
def unassign(
    exam_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    p: Principal = Depends(require_roles(Role.ADMIN)),
    db: Session = Depends(db_dep),
) -> None:
    visible_exam(db, p, exam_id)
    row = db.get(ExamAssignment, (exam_id, user_id))
    if row is not None:
        db.delete(row)
        _audit(db, p, request, "exam.unassign", "exam", str(exam_id), examiner_id=str(user_id))
        db.commit()
