"""Result reports (4.4): a student's result sheet (PDF) and the exam summary (CSV, PDF). Administrators and teachers only.

Reports are built from FINALIZED snapshots only and say which snapshot they came from. A booklet that is not finalized has no
result sheet (409), and the summary lists such booklets as left out instead of guessing. Every generation is audited with the
snapshot ids and the SHA-256 of what was produced. The CSV keeps the formula-injection guard of the totals export.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, require_roles
from grademind_api.errors import ApiError
from grademind_api.routes.exams import visible_exam
from grademind_api.routes.grading import _cell
from grademind_api.routes.submissions import visible_submission
from grademind_core.db.models import AuditLog, Role
from grademind_core.reports import sheet_for, student_sheet_document, summary_csv, summary_document, summary_for
from grademind_core.result_snapshots import current_snapshot
from grademind_core.security import Principal

router = APIRouter(tags=["reports"])
MANAGERS = (Role.ADMIN, Role.TEACHER)
NO_STORE = {"cache-control": "no-store"}


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:60] or "report"


def _audit(
    db: Session, p: Principal, request: Request, action: str, entity_type: str, entity_id: uuid.UUID, **details: Any
) -> None:
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            request_id=request.state.request_id,
            details=details,
        )
    )


@router.get("/submissions/{submission_id}/report.pdf")
def student_report(
    submission_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*MANAGERS)), db: Session = Depends(db_dep)
) -> Response:
    sub = visible_submission(db, p, submission_id)
    snap = current_snapshot(db, sub.id)
    if snap is None:
        raise ApiError(409, "not_finalized", "This result is not finalized, so it has no result sheet yet. Finalize it first.")
    now = datetime.now(UTC)
    doc = student_sheet_document(sheet_for(db, sub, snap), now)
    pdf = doc.render(now)
    _audit(
        db,
        p,
        request,
        "report.student_pdf",
        "submission",
        sub.id,
        exam_id=str(sub.exam_id),
        snapshot_id=str(snap.id),
        snapshot_no=snap.snapshot_no,
        sha256=hashlib.sha256(pdf).hexdigest(),
        bytes=len(pdf),
        unprintable_characters=doc.replaced,
    )
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "content-disposition": f'attachment; filename="result_{_safe(sub.student_ref)}_s{snap.snapshot_no}.pdf"',
            **NO_STORE,
        },
    )


@router.get("/exams/{exam_id}/summary.csv")
def summary_csv_export(
    exam_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*MANAGERS)), db: Session = Depends(db_dep)
) -> Response:
    exam = visible_exam(db, p, exam_id)
    data = summary_for(db, exam)
    body = summary_csv(data, _cell)
    _audit(
        db,
        p,
        request,
        "report.summary_csv",
        "exam",
        exam.id,
        exam_id=str(exam.id),
        rows=len(data.rows),
        left_out=len(data.not_finalized),
        snapshot_ids=[str(r.snapshot_id) for r in data.rows],
        sha256=hashlib.sha256(body.encode()).hexdigest(),
    )
    db.commit()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{_safe(exam.name)}_summary.csv"', **NO_STORE},
    )


@router.get("/exams/{exam_id}/summary.pdf")
def summary_pdf_export(
    exam_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*MANAGERS)), db: Session = Depends(db_dep)
) -> Response:
    exam = visible_exam(db, p, exam_id)
    data = summary_for(db, exam)
    now = datetime.now(UTC)
    doc = summary_document(data, now)
    pdf = doc.render(now)
    _audit(
        db,
        p,
        request,
        "report.summary_pdf",
        "exam",
        exam.id,
        exam_id=str(exam.id),
        rows=len(data.rows),
        left_out=len(data.not_finalized),
        snapshot_ids=[str(r.snapshot_id) for r in data.rows],
        sha256=hashlib.sha256(pdf).hexdigest(),
        unprintable_characters=doc.replaced,
    )
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"content-disposition": f'attachment; filename="{_safe(exam.name)}_summary.pdf"', **NO_STORE},
    )
