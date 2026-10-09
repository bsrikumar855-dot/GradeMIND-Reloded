"""Grading orchestration (D25 e/f/g): turns the examiner's regions and evaluations into ScoreComputer input and
persists the resulting score sheet. No arithmetic here: every mark comes from grademind_core.scoring.compute.

- Attempts come from live (not deleted) answer regions, grouped by (question, attempt number). An attempt is crossed
  out only if every region of it is crossed out.
- The current evaluation of an attempt is its latest row (highest seq); older rows are kept (append-only history).
- Evaluations whose attempt no longer exists (its regions were deleted) are ignored and reported as the
  ORPHANED_EVALUATIONS flag, never silently counted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_core.db.models import AnswerRegion, Evaluation, PaperVersion, RubricVersion, ScoreResult, VersionStatus
from grademind_core.grading import Paper, Policy, Rubric
from grademind_core.scoring import Attempt, ScoreSheet, Verdict, compute


@dataclass(frozen=True)
class GradingContext:
    rubric_version: RubricVersion
    paper_version: PaperVersion
    paper: Paper
    rubric: Rubric
    policy: Policy


def grading_context(db: Session, exam_id: uuid.UUID) -> GradingContext | None:
    """The latest APPROVED rubric of the exam and the paper version it was written against."""
    rv = db.scalar(
        select(RubricVersion)
        .where(RubricVersion.exam_id == exam_id, RubricVersion.status == VersionStatus.APPROVED)
        .order_by(RubricVersion.version_no.desc())
        .limit(1)
    )
    if rv is None:
        return None
    pv = db.get(PaperVersion, rv.paper_version_id)
    assert pv is not None
    return GradingContext(
        rv, pv, Paper.model_validate(pv.document), Rubric.model_validate(rv.document), Policy.model_validate(rv.policy)
    )


def live_regions(db: Session, submission_id: uuid.UUID) -> list[AnswerRegion]:
    return list(
        db.scalars(
            select(AnswerRegion)
            .where(AnswerRegion.submission_id == submission_id, AnswerRegion.deleted_at.is_(None))
            .order_by(AnswerRegion.created_at, AnswerRegion.id)
        )
    )


def attempts_from(regions: list[AnswerRegion]) -> list[Attempt]:
    groups: dict[tuple[str, int], list[AnswerRegion]] = {}
    for r in regions:
        groups.setdefault((r.qid, r.attempt_no), []).append(r)
    return [Attempt(q, a, crossed_out=all(r.crossed_out for r in rs)) for (q, a), rs in sorted(groups.items())]


def current_evaluations(db: Session, submission_id: uuid.UUID, rubric_version_id: uuid.UUID) -> dict[tuple[str, int], Evaluation]:
    rows = db.scalars(
        select(Evaluation)
        .where(Evaluation.submission_id == submission_id, Evaluation.rubric_version_id == rubric_version_id)
        .order_by(Evaluation.seq)
    )
    latest: dict[tuple[str, int], Evaluation] = {}
    for e in rows:
        latest[(e.qid, e.attempt_no)] = e
    return latest


def sheet_json(sheet: ScoreSheet) -> dict[str, Any]:
    return {
        qid: {
            "marks": str(n.marks),
            "max_marks": str(n.max_marks),
            "status": n.status.value,
            "counted_attempt": n.counted_attempt,
            "flags": list(n.flags),
        }
        for qid, n in sheet.nodes.items()
    }


def score(
    db: Session, submission_id: uuid.UUID, ctx: GradingContext, pending: dict[tuple[str, int], dict[str, str]] | None = None
) -> tuple[ScoreSheet, list[str]]:
    """Compute the current sheet (nothing written). `pending` = verdicts about to be saved, used in place of the
    stored ones for those attempts. Returns the sheet and the orchestration flags."""
    attempts = attempts_from(live_regions(db, submission_id))
    existing = {(a.qid, a.attempt_no) for a in attempts}
    chosen = {k: ev.verdicts for k, ev in current_evaluations(db, submission_id, ctx.rubric_version.id).items()}
    chosen.update(pending or {})
    verdicts: list[Verdict] = []
    orphaned = False
    for (qid, att), vs in chosen.items():
        if (qid, att) not in existing:
            orphaned = True
            continue
        verdicts.extend(Verdict(qid, att, c, lv) for c, lv in vs.items())
    sheet = compute(ctx.paper, ctx.rubric, ctx.policy, attempts, verdicts)
    return sheet, (["ORPHANED_EVALUATIONS"] if orphaned else [])


def record_score(
    db: Session,
    submission_id: uuid.UUID,
    ctx: GradingContext,
    actor_id: uuid.UUID,
    trigger_evaluation_id: uuid.UUID | None = None,
) -> ScoreResult:
    """Compute and append a ScoreResult in the caller's transaction (the caller commits evaluation + score together)."""
    sheet, extra = score(db, submission_id, ctx)
    row = ScoreResult(
        submission_id=submission_id,
        rubric_version_id=ctx.rubric_version.id,
        total=sheet.total,
        max_total=sheet.max_total,
        complete=sheet.complete,
        sheet=sheet_json(sheet),
        flags=sorted(set(sheet.flags) | set(extra)),
        score_computer_version=sheet.version,
        trigger_evaluation_id=trigger_evaluation_id,
        created_by=actor_id,
    )
    db.add(row)
    return row


def latest_score(db: Session, submission_id: uuid.UUID, rubric_version_id: uuid.UUID) -> ScoreResult | None:
    return db.scalar(
        select(ScoreResult)
        .where(ScoreResult.submission_id == submission_id, ScoreResult.rubric_version_id == rubric_version_id)
        .order_by(ScoreResult.seq.desc())
        .limit(1)
    )
