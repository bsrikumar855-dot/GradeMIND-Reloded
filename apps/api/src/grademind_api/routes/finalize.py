"""Finalize and reopen a booklet's result (4.2). Administrators and teachers only; examiners grade, they do not sign off.

Finalizing freezes the result into an append-only snapshot and makes the grades read-only (the grading routes answer 409 and the
database refuses the writes as a backstop). Reopening needs a reason and is audited; the next finalize adds a new snapshot.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, PlainSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, require_roles
from grademind_api.errors import ApiError
from grademind_api.routes.exams import visible_exam
from grademind_api.routes.submissions import visible_submission
from grademind_core.db.models import AuditLog, FinalizationEvent, ResultSnapshot, Role, Submission, User
from grademind_core.evaluation import GradingContext, grading_context
from grademind_core.result_snapshots import (
    FinalizationError,
    Readiness,
    current_snapshot,
    finalize,
    readiness,
    reopen,
    snapshots_of,
)
from grademind_core.security import Principal

router = APIRouter(tags=["finalize"])
MANAGERS = (Role.ADMIN, Role.TEACHER)
Marks = Annotated[Decimal, PlainSerializer(lambda d: format(d.normalize(), "f"), return_type=str)]


class FinalizeIn(BaseModel):
    confirm_not_attempted: bool = False


class ReopenIn(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class SnapshotOut(BaseModel):
    id: uuid.UUID
    snapshot_no: int
    paper_version_no: int
    rubric_version_no: int
    score_computer_version: str
    policy: dict[str, Any]
    total: Marks
    max_total: Marks
    complete: bool
    nodes: dict[str, Any]
    flags: list[str]
    finalized_by: str
    finalized_at: datetime


class EventOut(BaseModel):
    action: str
    at: datetime
    by: str
    reason: str | None
    snapshot_no: int


class IssueOut(BaseModel):
    code: str
    message: str


class NotAttemptedOut(BaseModel):
    qid: str
    label: str


class FinalizationOut(BaseModel):
    state: str  # "OPEN" | "FINALIZED"
    snapshot: SnapshotOut | None
    ready: bool
    blockers: list[IssueOut]
    not_attempted: list[NotAttemptedOut]
    history: list[EventOut]


def _names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    return {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def _snapshot_out(db: Session, s: ResultSnapshot) -> SnapshotOut:
    return SnapshotOut(
        id=s.id,
        snapshot_no=s.snapshot_no,
        paper_version_no=s.paper_version_no,
        rubric_version_no=s.rubric_version_no,
        score_computer_version=s.score_computer_version,
        policy=s.policy,
        total=s.total,
        max_total=s.max_total,
        complete=s.complete,
        nodes=s.sheet,
        flags=list(s.flags),
        finalized_by=_names(db, {s.created_by}).get(s.created_by, "?"),
        finalized_at=s.created_at,
    )


def _state(db: Session, sub: Submission, ctx: GradingContext | None) -> FinalizationOut:
    events = list(
        db.scalars(select(FinalizationEvent).where(FinalizationEvent.submission_id == sub.id).order_by(FinalizationEvent.seq))
    )
    snaps = {s.id: s for s in snapshots_of(db, sub.id)}
    names = _names(db, {e.actor_id for e in events})
    history = [
        EventOut(
            action=e.action,
            at=e.created_at,
            by=names.get(e.actor_id, "?"),
            reason=e.reason,
            snapshot_no=snaps[e.snapshot_id].snapshot_no,
        )
        for e in events
    ]
    snap = current_snapshot(db, sub.id)
    if snap is not None:
        return FinalizationOut(
            state="FINALIZED", snapshot=_snapshot_out(db, snap), ready=False, blockers=[], not_attempted=[], history=history
        )
    if ctx is None:
        r = Readiness(
            blockers=[{"code": "no_rubric", "message": "This exam has no approved rubric yet, so nothing can be graded."}]
        )
    else:
        r = readiness(db, sub.id, ctx)
    return FinalizationOut(
        state="OPEN",
        snapshot=None,
        ready=r.ready,
        blockers=[IssueOut(**b) for b in r.blockers],
        not_attempted=[NotAttemptedOut(**n) for n in r.not_attempted],
        history=history,
    )


def _audit(
    db: Session, p: Principal, request: Request, sub: Submission, action: str, entity_id: uuid.UUID, **details: Any
) -> None:
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action=action,
            entity_type="submission",
            entity_id=str(entity_id),
            request_id=request.state.request_id,
            details={"exam_id": str(sub.exam_id), "submission_id": str(sub.id), **details},
        )
    )


def _refuse(e: FinalizationError, status: int = 409) -> ApiError:
    return ApiError(
        status, e.code, e.message, [{"path": "", "code": b["code"], "message": b["message"]} for b in e.blockers] or None
    )


@router.get("/submissions/{submission_id}/finalization", response_model=FinalizationOut)
def get_finalization(
    submission_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)
) -> FinalizationOut:
    sub = visible_submission(db, p, submission_id)
    return _state(db, sub, grading_context(db, sub.exam_id))


@router.get("/submissions/{submission_id}/snapshots", response_model=list[SnapshotOut])
def list_snapshots(
    submission_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)
) -> list[SnapshotOut]:
    sub = visible_submission(db, p, submission_id)
    return [_snapshot_out(db, s) for s in snapshots_of(db, sub.id)]


def _finalize_one(db: Session, p: Principal, request: Request, sub: Submission, confirm: bool) -> SnapshotOut:
    ctx = grading_context(db, sub.exam_id)
    if ctx is None:
        raise ApiError(409, "rubric_not_approved", "This exam has no approved rubric yet, so it cannot be finalized.")
    try:
        snap = finalize(db, sub, ctx, p.user_id, confirm)
    except FinalizationError as e:
        db.rollback()
        raise _refuse(e) from e
    _audit(
        db,
        p,
        request,
        sub,
        "submission.finalize",
        sub.id,
        snapshot_id=str(snap.id),
        snapshot_no=snap.snapshot_no,
        total=str(snap.total),
        max_total=str(snap.max_total),
        rubric_version_no=snap.rubric_version_no,
        score_computer_version=snap.score_computer_version,
        confirmed_not_attempted=confirm,
    )
    db.commit()
    return _snapshot_out(db, snap)


@router.post("/submissions/{submission_id}/finalize", response_model=SnapshotOut, status_code=201)
def finalize_submission(
    submission_id: uuid.UUID,
    body: FinalizeIn,
    request: Request,
    p: Principal = Depends(require_roles(*MANAGERS)),
    db: Session = Depends(db_dep),
) -> SnapshotOut:
    sub = visible_submission(db, p, submission_id)
    return _finalize_one(db, p, request, sub, body.confirm_not_attempted)


@router.post("/submissions/{submission_id}/reopen", response_model=FinalizationOut)
def reopen_submission(
    submission_id: uuid.UUID,
    body: ReopenIn,
    request: Request,
    p: Principal = Depends(require_roles(*MANAGERS)),
    db: Session = Depends(db_dep),
) -> FinalizationOut:
    sub = visible_submission(db, p, submission_id)
    try:
        ev = reopen(db, sub, p.user_id, body.reason)
    except FinalizationError as e:
        db.rollback()
        raise _refuse(e, 422 if e.code == "reason_required" else 409) from e
    snap = db.get(ResultSnapshot, ev.snapshot_id)
    assert snap is not None
    _audit(
        db, p, request, sub, "submission.reopen", sub.id, snapshot_id=str(snap.id), snapshot_no=snap.snapshot_no, reason=ev.reason
    )
    db.commit()
    return _state(db, sub, grading_context(db, sub.exam_id))


class BulkIssue(BaseModel):
    submission_id: uuid.UUID
    student_ref: str
    code: str
    message: str


class BulkOut(BaseModel):
    finalized: int
    skipped: list[BulkIssue]


@router.post("/exams/{exam_id}/finalize", response_model=BulkOut)
def finalize_exam(
    exam_id: uuid.UUID,
    body: FinalizeIn,
    request: Request,
    p: Principal = Depends(require_roles(*MANAGERS)),
    db: Session = Depends(db_dep),
) -> BulkOut:
    """Finalize every booklet of the exam that is ready. Booklets that are not ready (or already final) are listed, not failed."""
    visible_exam(db, p, exam_id)
    subs = list(
        db.scalars(
            select(Submission).where(Submission.exam_id == exam_id).order_by(Submission.student_ref, Submission.created_at)
        )
    )
    done, skipped = 0, []
    for sub in subs:
        try:
            _finalize_one(db, p, request, sub, body.confirm_not_attempted)
            done += 1
        except ApiError as e:
            if e.code == "rubric_not_approved":
                raise
            msg = e.message if not e.issues else f"{e.message} " + " ".join(i["message"] for i in e.issues)
            skipped.append(BulkIssue(submission_id=sub.id, student_ref=sub.student_ref, code=e.code, message=msg))
    return BulkOut(finalized=done, skipped=skipped)
