"""Exams with RBAC (spec §16): admins/teachers see their org's exams; examiners only exams assigned to them."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, require_roles
from grademind_api.errors import ApiError
from grademind_core.db.models import AuditLog, Exam, ExamAssignment, Role, User
from grademind_core.security import Principal

router = APIRouter(tags=["exams"])


class ExamIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    subject: str = Field(min_length=1, max_length=200)
    course: str | None = Field(default=None, max_length=200)
    total_marks: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    policy: dict[str, Any] = Field(default_factory=dict)


class ExamOut(BaseModel):
    id: uuid.UUID
    name: str
    subject: str
    course: str | None
    total_marks: Decimal
    policy: dict[str, Any]


class AssignIn(BaseModel):
    user_id: uuid.UUID


def _out(e: Exam) -> ExamOut:
    return ExamOut(id=e.id, name=e.name, subject=e.subject, course=e.course, total_marks=e.total_marks, policy=e.policy)


def visible_exam(db: Session, p: Principal, exam_id: uuid.UUID) -> Exam:
    """The single visibility rule. Out-of-scope exams return 404, so their existence is not revealed."""
    e = db.get(Exam, exam_id)
    if e is None or e.org_id != p.org_id:
        raise ApiError(404, "not_found", "Exam not found.")
    if p.role == Role.EXAMINER and db.get(ExamAssignment, (exam_id, p.user_id)) is None:
        raise ApiError(404, "not_found", "Exam not found.")
    return e


@router.get("/exams", response_model=list[ExamOut])
def list_exams(p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> list[ExamOut]:
    q = select(Exam).where(Exam.org_id == p.org_id).order_by(Exam.created_at)
    if p.role == Role.EXAMINER:
        q = q.join(ExamAssignment, ExamAssignment.exam_id == Exam.id).where(ExamAssignment.user_id == p.user_id)
    return [_out(e) for e in db.scalars(q)]


@router.post("/exams", response_model=ExamOut, status_code=201)
def create_exam(
    body: ExamIn, p: Principal = Depends(require_roles(Role.ADMIN, Role.TEACHER)), db: Session = Depends(db_dep)
) -> ExamOut:
    e = Exam(org_id=p.org_id, created_by=p.user_id, **body.model_dump())
    db.add(e)
    db.flush()
    db.add(AuditLog(actor_id=p.user_id, action="exam.create", entity_type="exam", entity_id=str(e.id)))
    db.commit()  # exam + audit row in one transaction
    return _out(e)


@router.get("/exams/{exam_id}", response_model=ExamOut)
def get_exam(exam_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> ExamOut:
    return _out(visible_exam(db, p, exam_id))


@router.post("/exams/{exam_id}/assignments", status_code=204)
def assign(
    exam_id: uuid.UUID, body: AssignIn, p: Principal = Depends(require_roles(Role.ADMIN)), db: Session = Depends(db_dep)
) -> None:
    visible_exam(db, p, exam_id)
    u = db.get(User, body.user_id)
    if u is None or u.org_id != p.org_id or u.role != Role.EXAMINER:
        raise ApiError(422, "invalid_assignee", "Only examiners in your organisation can be assigned.")
    if db.get(ExamAssignment, (exam_id, u.id)) is None:
        db.add(ExamAssignment(exam_id=exam_id, user_id=u.id))
        db.add(
            AuditLog(
                actor_id=p.user_id,
                action="exam.assign",
                entity_type="exam",
                entity_id=str(exam_id),
                details={"examiner_id": str(u.id)},
            )
        )
        db.commit()
