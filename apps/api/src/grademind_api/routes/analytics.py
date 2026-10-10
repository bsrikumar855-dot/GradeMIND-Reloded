"""Exam analytics (4.3). Read-only; administrators and teachers only. From verdicts and stored results, never from OCR."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, require_roles
from grademind_api.errors import ApiError
from grademind_api.routes.exams import visible_exam
from grademind_core.analytics import exam_analytics
from grademind_core.db.models import Role
from grademind_core.evaluation import grading_context
from grademind_core.security import Principal

router = APIRouter(tags=["analytics"])


@router.get("/exams/{exam_id}/analytics")
def analytics(
    exam_id: uuid.UUID,
    scope: Annotated[Literal["all", "finalized"], Query()] = "all",
    p: Principal = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
    db: Session = Depends(db_dep),
) -> dict[str, Any]:
    visible_exam(db, p, exam_id)
    ctx = grading_context(db, exam_id)
    if ctx is None:
        raise ApiError(409, "rubric_not_approved", "This exam has no approved rubric yet, so there is nothing to analyse.")
    return exam_analytics(db, exam_id, ctx, scope)
