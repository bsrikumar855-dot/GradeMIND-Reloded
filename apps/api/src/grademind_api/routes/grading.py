"""Examiner grading workspace API (D25 e/f/g).

- Regions: draw a box on a page, assign it to a question (attempt). Soft delete; crossed-out flag.
- Evaluations: a verdict level per criterion; ScoreComputer computes the marks. Append-only: a re-grade or an
  override is a new row. Changing someone else's current evaluation is an OVERRIDE and needs a reason.
- Every write recomputes the whole score sheet and stores it in the SAME transaction (never partially written).
- Totals (JSON + CSV) and the exam audit trail.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, PlainSerializer, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, require_roles, store_dep
from grademind_api.errors import ApiError
from grademind_api.routes.exams import visible_exam
from grademind_api.routes.submissions import PageOut, list_pages, visible_submission
from grademind_core.db.models import AnswerRegion, AuditLog, Evaluation, Page, Role, Submission
from grademind_core.evaluation import (
    GradingContext,
    current_evaluations,
    grading_context,
    latest_score,
    live_regions,
    record_score,
    score,
    sheet_json,
)
from grademind_core.grading import iter_nodes, leaves, node_max
from grademind_core.result_snapshots import current_snapshot, is_finalized
from grademind_core.security import Principal
from grademind_core.storage import ObjectStore

router = APIRouter(tags=["grading"])

# marks as canonical decimal strings: 2, 1.5, 20 (never 2.0000 or 2E+1)
Marks = Annotated[Decimal, PlainSerializer(lambda d: format(d.normalize(), "f"), return_type=str)]
GRADERS = (Role.ADMIN, Role.TEACHER, Role.EXAMINER)


class RegionIn(BaseModel):
    page_id: uuid.UUID
    bbox: list[float] = Field(min_length=4, max_length=4)
    qid: str = Field(min_length=1, max_length=40)
    new_attempt: bool = False  # default: continue the question's latest attempt (an answer spanning pages)
    crossed_out: bool = False

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, v: list[float]) -> list[float]:
        x0, y0, x1, y1 = v
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("bbox must be [x0, y0, x1, y1] with 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
        if (x1 - x0) < 0.005 or (y1 - y0) < 0.005:
            raise ValueError("the box is too small")
        return [round(c, 5) for c in v]


class RegionOut(BaseModel):
    id: uuid.UUID
    page_id: uuid.UUID
    bbox: list[float]
    qid: str
    attempt_no: int
    crossed_out: bool


class CrossedIn(BaseModel):
    crossed_out: bool


class EvaluationIn(BaseModel):
    verdicts: dict[str, str] = Field(default_factory=dict)  # criterion id -> level id (partial allowed: INCOMPLETE)
    notes: str = Field(default="", max_length=4000)
    evidence_region_id: uuid.UUID | None = None
    override_reason: str | None = Field(default=None, max_length=2000)


class EvaluationOut(BaseModel):
    id: uuid.UUID
    qid: str
    attempt_no: int
    verdicts: dict[str, str]
    notes: str
    evidence_region_id: uuid.UUID | None
    marks: Marks
    examiner_id: uuid.UUID
    is_override: bool
    override_reason: str | None
    supersedes_id: uuid.UUID | None
    created_at: datetime


class ScoreOut(BaseModel):
    total: Marks
    max_total: Marks
    complete: bool
    nodes: dict[str, Any]
    flags: list[str]
    version: str


class FinalizationSummary(BaseModel):
    state: str  # "OPEN" | "FINALIZED": a finalized result is read-only (4.2)
    snapshot_no: int | None


class Workspace(BaseModel):
    submission_id: uuid.UUID
    exam_id: uuid.UUID
    student_ref: str
    rubric_version_id: uuid.UUID
    paper: dict[str, Any]
    rubric: dict[str, Any]
    policy: dict[str, Any]
    pages: list[PageOut]
    regions: list[RegionOut]
    evaluations: list[EvaluationOut]
    score: ScoreOut
    finalization: FinalizationSummary


class SaveOut(BaseModel):
    evaluation: EvaluationOut
    score: ScoreOut


def _finalization_summary(db: Session, sub: Submission) -> FinalizationSummary:
    snap = current_snapshot(db, sub.id)
    return FinalizationSummary(state="FINALIZED" if snap else "OPEN", snapshot_no=snap.snapshot_no if snap else None)


def _open(db: Session, sub: Submission) -> None:
    """A finalized result is read-only (4.2). The database refuses the writes too; this gives the examiner the reason."""
    if is_finalized(db, sub.id):
        raise ApiError(409, "finalized", "This result is finalized. Reopen it, with a reason, to change anything.")


def _ctx(db: Session, sub: Submission) -> GradingContext:
    ctx = grading_context(db, sub.exam_id)
    if ctx is None:
        raise ApiError(409, "rubric_not_approved", "This exam has no approved rubric yet, so it cannot be graded.")
    return ctx


def _region_out(r: AnswerRegion) -> RegionOut:
    return RegionOut(id=r.id, page_id=r.page_id, bbox=r.bbox, qid=r.qid, attempt_no=r.attempt_no, crossed_out=r.crossed_out)


def _eval_out(e: Evaluation) -> EvaluationOut:
    return EvaluationOut(
        id=e.id,
        qid=e.qid,
        attempt_no=e.attempt_no,
        verdicts=e.verdicts,
        notes=e.notes,
        evidence_region_id=e.evidence_region_id,
        marks=e.marks,
        examiner_id=e.examiner_id,
        is_override=e.is_override,
        override_reason=e.override_reason,
        supersedes_id=e.supersedes_id,
        created_at=e.created_at,
    )


def _score_out(db: Session, sub: Submission, ctx: GradingContext) -> ScoreOut:
    sheet, extra = score(db, sub.id, ctx)
    return ScoreOut(
        total=sheet.total,
        max_total=sheet.max_total,
        complete=sheet.complete,
        nodes=sheet_json(sheet),
        flags=sorted(set(sheet.flags) | set(extra)),
        version=sheet.version,
    )


def _audit(
    db: Session, p: Principal, request: Request, sub: Submission, action: str, entity: str, eid: uuid.UUID, **kw: Any
) -> None:
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action=action,
            entity_type=entity,
            entity_id=str(eid),
            request_id=request.state.request_id,
            details={"exam_id": str(sub.exam_id), "submission_id": str(sub.id), **kw},
        )
    )


@router.get("/submissions/{submission_id}/workspace", response_model=Workspace)
def workspace(
    submission_id: uuid.UUID,
    p: Principal = Depends(principal_dep),
    db: Session = Depends(db_dep),
    store: ObjectStore = Depends(store_dep),
) -> Workspace:
    sub = visible_submission(db, p, submission_id)
    ctx = _ctx(db, sub)
    return Workspace(
        submission_id=sub.id,
        exam_id=sub.exam_id,
        student_ref=sub.student_ref,
        rubric_version_id=ctx.rubric_version.id,
        paper=ctx.paper_version.document,
        rubric=ctx.rubric_version.document,
        policy=ctx.rubric_version.policy,
        pages=list_pages(submission_id, p, db, store),
        regions=[_region_out(r) for r in live_regions(db, sub.id)],
        evaluations=[_eval_out(e) for e in current_evaluations(db, sub.id, ctx.rubric_version.id).values()],
        score=_score_out(db, sub, ctx),
        finalization=_finalization_summary(db, sub),
    )


@router.post("/submissions/{submission_id}/regions", response_model=RegionOut, status_code=201)
def create_region(
    submission_id: uuid.UUID,
    body: RegionIn,
    request: Request,
    p: Principal = Depends(require_roles(*GRADERS)),
    db: Session = Depends(db_dep),
) -> RegionOut:
    sub = visible_submission(db, p, submission_id)
    _open(db, sub)
    ctx = _ctx(db, sub)
    page = db.get(Page, body.page_id)
    if page is None or page.submission_id != sub.id:
        raise ApiError(422, "invalid_page", "That page does not belong to this booklet.")
    if body.qid not in {n.id for n in leaves(ctx.paper)}:
        raise ApiError(422, "invalid_question", "Answers can only be assigned to questions that are marked directly.")
    existing = [r.attempt_no for r in live_regions(db, sub.id) if r.qid == body.qid]
    attempt = (max(existing) + 1 if body.new_attempt else max(existing)) if existing else 1
    r = AnswerRegion(
        submission_id=sub.id,
        page_id=page.id,
        bbox=body.bbox,
        qid=body.qid,
        attempt_no=attempt,
        crossed_out=body.crossed_out,
        created_by=p.user_id,
    )
    db.add(r)
    db.flush()
    _audit(db, p, request, sub, "region.create", "answer_region", r.id, qid=body.qid, attempt_no=attempt, page_no=page.page_no)
    record_score(db, sub.id, ctx, p.user_id)
    db.commit()
    return _region_out(r)


def _live_region(db: Session, sub: Submission, region_id: uuid.UUID) -> AnswerRegion:
    r = db.get(AnswerRegion, region_id)
    if r is None or r.submission_id != sub.id or r.deleted_at is not None:
        raise ApiError(404, "not_found", "Region not found.")
    return r


@router.delete("/submissions/{submission_id}/regions/{region_id}", status_code=204)
def delete_region(
    submission_id: uuid.UUID,
    region_id: uuid.UUID,
    request: Request,
    p: Principal = Depends(require_roles(*GRADERS)),
    db: Session = Depends(db_dep),
) -> None:
    sub = visible_submission(db, p, submission_id)
    _open(db, sub)
    ctx = _ctx(db, sub)
    r = _live_region(db, sub, region_id)
    r.deleted_at, r.deleted_by = datetime.now(UTC), p.user_id
    _audit(db, p, request, sub, "region.delete", "answer_region", r.id, qid=r.qid, attempt_no=r.attempt_no)
    record_score(db, sub.id, ctx, p.user_id)
    db.commit()


@router.post("/submissions/{submission_id}/regions/{region_id}/crossed", response_model=RegionOut)
def set_crossed(
    submission_id: uuid.UUID,
    region_id: uuid.UUID,
    body: CrossedIn,
    request: Request,
    p: Principal = Depends(require_roles(*GRADERS)),
    db: Session = Depends(db_dep),
) -> RegionOut:
    sub = visible_submission(db, p, submission_id)
    _open(db, sub)
    ctx = _ctx(db, sub)
    r = _live_region(db, sub, region_id)
    r.crossed_out = body.crossed_out
    _audit(db, p, request, sub, "region.crossed_out", "answer_region", r.id, crossed_out=body.crossed_out)
    record_score(db, sub.id, ctx, p.user_id)
    db.commit()
    return _region_out(r)


@router.put("/submissions/{submission_id}/evaluations/{qid}/{attempt_no}", response_model=SaveOut)
def save_evaluation(
    submission_id: uuid.UUID,
    qid: str,
    attempt_no: int,
    body: EvaluationIn,
    request: Request,
    p: Principal = Depends(require_roles(*GRADERS)),
    db: Session = Depends(db_dep),
) -> SaveOut:
    sub = visible_submission(db, p, submission_id)
    _open(db, sub)
    ctx = _ctx(db, sub)
    regions = [r for r in live_regions(db, sub.id) if r.qid == qid and r.attempt_no == attempt_no]
    if not regions:
        raise ApiError(422, "no_answer", "Map the answer on the page (draw a box) before grading this question.")
    if body.evidence_region_id is not None and body.evidence_region_id not in {r.id for r in live_regions(db, sub.id)}:
        raise ApiError(422, "invalid_evidence", "The evidence must be one of this booklet's answer regions.")
    criteria = {c.id: {lv.id for lv in c.levels} for qr in ctx.rubric.questions if qr.qid == qid for c in qr.criteria}
    bad = [f"{c}/{lv}" for c, lv in body.verdicts.items() if c not in criteria or lv not in criteria[c]]
    if bad:
        raise ApiError(
            422,
            "invalid_verdict",
            "Some verdicts do not match the rubric.",
            [{"path": b, "code": "unknown", "message": "unknown criterion or level"} for b in bad],
        )
    current = current_evaluations(db, sub.id, ctx.rubric_version.id).get((qid, attempt_no))
    is_override = current is not None and current.examiner_id != p.user_id
    if is_override and not (body.override_reason or "").strip():
        raise ApiError(
            422, "override_reason_required", "Another examiner graded this answer. Give a reason for the change (override)."
        )
    verdicts = dict(sorted(body.verdicts.items()))
    sheet, _ = score(db, sub.id, ctx, pending={(qid, attempt_no): verdicts})  # inputs validated above
    ev = Evaluation(
        submission_id=sub.id,
        rubric_version_id=ctx.rubric_version.id,
        qid=qid,
        attempt_no=attempt_no,
        verdicts=verdicts,
        notes=body.notes,
        evidence_region_id=body.evidence_region_id,
        marks=sheet.nodes[qid].marks,  # this question's marks after the save, from the ScoreComputer
        examiner_id=p.user_id,
        supersedes_id=current.id if current else None,
        is_override=is_override,
        override_reason=(body.override_reason or "").strip() or None,
        score_computer_version=sheet.version,
    )
    db.add(ev)
    db.flush()
    action = "evaluation.override" if is_override else "evaluation.save"
    _audit(
        db,
        p,
        request,
        sub,
        action,
        "evaluation",
        ev.id,
        qid=qid,
        attempt_no=attempt_no,
        marks=str(ev.marks),
        supersedes=str(ev.supersedes_id) if ev.supersedes_id else None,
    )
    record_score(db, sub.id, ctx, p.user_id, trigger_evaluation_id=ev.id)
    db.commit()  # evaluation + score sheet + audit row: one transaction
    return SaveOut(evaluation=_eval_out(ev), score=_score_out(db, sub, ctx))


@router.get("/submissions/{submission_id}/evaluations/{qid}/{attempt_no}/history", response_model=list[EvaluationOut])
def evaluation_history(
    submission_id: uuid.UUID, qid: str, attempt_no: int, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)
) -> list[EvaluationOut]:
    sub = visible_submission(db, p, submission_id)
    rows = db.scalars(
        select(Evaluation)
        .where(Evaluation.submission_id == sub.id, Evaluation.qid == qid, Evaluation.attempt_no == attempt_no)
        .order_by(Evaluation.seq)
    )
    return [_eval_out(e) for e in rows]


# ------------------------------------------------------------------------------------------------------ totals


class TotalsRow(BaseModel):
    submission_id: uuid.UUID
    student_ref: str
    total: Marks | None
    max_total: Marks
    complete: bool
    sections: dict[str, Marks]  # top-level node id -> marks
    flags: list[str]
    finalized: bool = False  # the result is frozen in a snapshot (4.2)
    snapshot_no: int | None = None


class Totals(BaseModel):
    exam_id: uuid.UUID
    rubric_version_id: uuid.UUID | None
    columns: list[dict[str, str]]  # top-level nodes: id, label, max
    rows: list[TotalsRow]


def _totals(db: Session, exam_id: uuid.UUID) -> Totals:
    ctx = grading_context(db, exam_id)
    subs = list(
        db.scalars(
            select(Submission).where(Submission.exam_id == exam_id).order_by(Submission.student_ref, Submission.created_at)
        )
    )
    if ctx is None:
        return Totals(exam_id=exam_id, rubric_version_id=None, columns=[], rows=[])
    tops = [(n.id, n.label) for path, n, depth in iter_nodes(ctx.paper.questions) if depth == 1]
    rows: list[TotalsRow] = []
    max_total = ctx.paper.total_marks
    for s in subs:
        snap = current_snapshot(db, s.id)
        res = latest_score(db, s.id, ctx.rubric_version.id)
        if res is None:
            rows.append(
                TotalsRow(
                    submission_id=s.id,
                    student_ref=s.student_ref,
                    total=None,
                    max_total=max_total,
                    complete=False,
                    sections={},
                    flags=[],
                )
            )
            continue
        rows.append(
            TotalsRow(
                submission_id=s.id,
                student_ref=s.student_ref,
                total=res.total,
                max_total=res.max_total,
                complete=res.complete,
                sections={qid: Decimal(res.sheet[qid]["marks"]) for qid, _ in tops if qid in res.sheet},
                flags=res.flags,
                finalized=snap is not None,
                snapshot_no=snap.snapshot_no if snap else None,
            )
        )
    top_nodes = {n.id: n for n in ctx.paper.questions}
    columns = [{"id": qid, "label": label, "max": str(node_max(top_nodes[qid]))} for qid, label in tops]
    return Totals(exam_id=exam_id, rubric_version_id=ctx.rubric_version.id, columns=columns, rows=rows)


@router.get("/exams/{exam_id}/totals", response_model=Totals)
def totals(exam_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> Totals:
    visible_exam(db, p, exam_id)
    return _totals(db, exam_id)


def _fmt(d: Decimal | None) -> str:
    return "" if d is None else format(d.normalize(), "f")


def _cell(v: object) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # CSV/formula injection guard


@router.get("/exams/{exam_id}/totals.csv")
def totals_csv(
    exam_id: uuid.UUID, request: Request, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)
) -> Response:
    exam = visible_exam(db, p, exam_id)
    t = _totals(db, exam_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["student_ref"] + [f"{c['label']} (/{c['max']})" for c in t.columns] + ["total", "max_total", "complete", "flags"])
    for r in t.rows:
        w.writerow(
            [_cell(r.student_ref)]
            + [_cell(_fmt(r.sections.get(c["id"]))) for c in t.columns]
            + [_cell(_fmt(r.total)), _cell(_fmt(r.max_total)), "yes" if r.complete else "no", _cell(" ".join(r.flags))]
        )
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action="totals.export_csv",
            entity_type="exam",
            entity_id=str(exam.id),
            request_id=request.state.request_id,
            details={"exam_id": str(exam.id), "rows": len(t.rows)},
        )
    )
    db.commit()
    safe = "".join(ch if ch.isalnum() else "_" for ch in exam.name)[:60] or "exam"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{safe}_totals.csv"'},
    )


# ------------------------------------------------------------------------------------------------------- audit


class AuditOut(BaseModel):
    at: datetime
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict[str, Any]


@router.get("/exams/{exam_id}/audit", response_model=list[AuditOut])
def exam_audit(
    exam_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    p: Principal = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
    db: Session = Depends(db_dep),
) -> list[AuditOut]:
    visible_exam(db, p, exam_id)
    eid = str(exam_id)
    rows = db.scalars(
        select(AuditLog)
        .where(or_(AuditLog.entity_id == eid, AuditLog.details["exam_id"].astext == eid))
        .order_by(AuditLog.at, AuditLog.id)
        .limit(limit)
    )
    return [
        AuditOut(
            at=a.at, actor_id=a.actor_id, action=a.action, entity_type=a.entity_type, entity_id=a.entity_id, details=a.details
        )
        for a in rows
    ]
