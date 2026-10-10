"""Finalize, reopen and verify a booklet's result (4.2; I8, I9, I12).

FINALIZED means the examiner's grades are read-only and the result is frozen into an append-only `ResultSnapshot`: the
rubric and paper versions, the ScoreComputer version, the policy, the answer attempts, which evaluation rows were current,
a digest of those inputs, and the frozen outputs (per-question marks, total). Reopening needs a reason and is recorded; the
next finalize adds snapshot n+1. Nothing is ever edited or deleted.

No arithmetic here: marks come from `grademind_core.scoring` (through `evaluation.score` when freezing, and directly when
verifying). `verify_snapshot` rebuilds the ScoreComputer input from the RAW rows (the evaluation rows the snapshot points at,
the stored attempts, the approved paper and rubric versions) and compares the recomputed result with the frozen one. Any
difference is reported, never repaired.

This module is on the grading side of the OCR boundary: it must not import anything OCR (the import contract proves it).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_core.db.models import (
    Evaluation,
    FinalizationEvent,
    PaperVersion,
    ResultSnapshot,
    RubricVersion,
    Submission,
)
from grademind_core.evaluation import GradingContext, attempts_from, current_evaluations, live_regions, score, sheet_json
from grademind_core.grading import Paper, PaperNode, Policy, Rubric
from grademind_core.scoring import VERSION as SCORE_COMPUTER_VERSION
from grademind_core.scoring import Attempt, NodeStatus, ScoreSheet, Verdict, compute

MIN_REOPEN_REASON = 10


class FinalizationError(Exception):
    """A finalize / reopen that cannot happen. `code` is stable; `message` is for the user."""

    def __init__(self, code: str, message: str, blockers: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.blockers = code, message, blockers or []


@dataclass
class Readiness:
    blockers: list[dict[str, str]] = field(default_factory=list)  # why it cannot be finalized
    not_attempted: list[dict[str, str]] = field(default_factory=list)  # questions with no answer box: they count as 0 marks
    sheet: ScoreSheet | None = None

    @property
    def ready(self) -> bool:
        return not self.blockers


def current_event(db: Session, submission_id: uuid.UUID) -> FinalizationEvent | None:
    return db.scalar(
        select(FinalizationEvent)
        .where(FinalizationEvent.submission_id == submission_id)
        .order_by(FinalizationEvent.seq.desc())
        .limit(1)
    )


def is_finalized(db: Session, submission_id: uuid.UUID) -> bool:
    ev = current_event(db, submission_id)
    return ev is not None and ev.action == "FINALIZED"


def current_snapshot(db: Session, submission_id: uuid.UUID) -> ResultSnapshot | None:
    """The snapshot that is in force: the one of the latest FINALIZED event, only while the booklet is finalized."""
    ev = current_event(db, submission_id)
    return db.get(ResultSnapshot, ev.snapshot_id) if ev is not None and ev.action == "FINALIZED" else None


def snapshots_of(db: Session, submission_id: uuid.UUID) -> list[ResultSnapshot]:
    return list(
        db.scalars(
            select(ResultSnapshot).where(ResultSnapshot.submission_id == submission_id).order_by(ResultSnapshot.snapshot_no)
        )
    )


def _leaf_labels(paper: Paper) -> dict[str, tuple[str, list[PaperNode]]]:
    """Every leaf's id -> (label, its OR-group ancestors)."""
    out: dict[str, tuple[str, list[PaperNode]]] = {}

    def walk(nodes: tuple[PaperNode, ...], groups: list[PaperNode]) -> None:
        for n in nodes:
            if n.children:
                walk(n.children, groups + [n] if n.choose is not None else groups)
            else:
                out[n.id] = (n.label, groups)

    walk(paper.questions, [])
    return out


def readiness(db: Session, submission_id: uuid.UUID, ctx: GradingContext) -> Readiness:
    """Can this booklet be finalized? Every answer that was mapped must be fully graded, and nothing may be orphaned.
    Questions with no answer box are not blockers (a student may skip a question) but are listed, so a person confirms them."""
    sheet, extra = score(db, submission_id, ctx)
    r = Readiness(sheet=sheet)
    attempts = attempts_from(live_regions(db, submission_id))
    if not attempts:
        r.blockers.append({"code": "no_answers", "message": "No answer boxes have been drawn yet, so nothing has been graded."})
    labels = _leaf_labels(ctx.paper)
    for qid, node in sheet.nodes.items():
        if node.status == NodeStatus.INCOMPLETE and qid in labels:
            r.blockers.append(
                {
                    "code": "incomplete",
                    "message": f"Question {labels[qid][0]} has an answer without a verdict for every criterion.",
                }
            )
    for qid, node in sheet.nodes.items():
        # an excluded OR alternative that is still incomplete is shown by the scorer; it does not block the counted result
        if node.status == NodeStatus.EXCLUDED_BY_CHOICE and "INCOMPLETE" in node.flags and qid in labels:
            r.blockers.append(
                {
                    "code": "incomplete",
                    "message": f"Question {labels[qid][0]} (an alternative) is graded in part only. Finish it or remove its box.",
                }
            )
    if "ORPHANED_EVALUATIONS" in extra:
        r.blockers.append(
            {
                "code": "orphaned_grades",
                "message": "A graded answer lost its answer box. Draw it again or ignore it by reopening the question.",
            }
        )
    attempted_groups: dict[str, int] = {}
    for qid, node in sheet.nodes.items():
        if qid in labels and node.status in (NodeStatus.SCORED, NodeStatus.INCOMPLETE, NodeStatus.EXCLUDED_BY_CHOICE):
            for g in labels[qid][1]:
                attempted_groups[g.id] = attempted_groups.get(g.id, 0) + 1
    for qid, node in sheet.nodes.items():
        if qid not in labels or node.status != NodeStatus.NOT_ATTEMPTED:
            continue
        label, groups = labels[qid]
        if any(attempted_groups.get(g.id, 0) >= (g.choose or 1) for g in groups):
            continue  # an unused alternative of an OR choice that is already answered
        r.not_attempted.append({"qid": qid, "label": label})
    return r


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def inputs_digest(attempts: list[dict[str, Any]], verdicts: dict[str, dict[str, str]], evaluation_ids: list[str]) -> str:
    return hashlib.sha256(
        _canonical({"attempts": attempts, "verdicts": verdicts, "evaluations": evaluation_ids}).encode()
    ).hexdigest()


def _frozen_inputs(
    attempts: list[Attempt], evals: dict[tuple[str, int], Evaluation]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, str]]]:
    att = [{"qid": a.qid, "attempt_no": a.attempt_no, "crossed_out": a.crossed_out} for a in attempts]
    live = {(a.qid, a.attempt_no) for a in attempts}
    refs = [{"qid": q, "attempt_no": n, "evaluation_id": str(e.id)} for (q, n), e in sorted(evals.items()) if (q, n) in live]
    verdicts = {f"{q}#{n}": dict(sorted(e.verdicts.items())) for (q, n), e in sorted(evals.items()) if (q, n) in live}
    return att, refs, verdicts


def finalize(
    db: Session, sub: Submission, ctx: GradingContext, actor_id: uuid.UUID, confirm_not_attempted: bool
) -> ResultSnapshot:
    """Freeze the booklet's result. The caller commits (snapshot, event and audit row in one transaction)."""
    # lock first: a grading write in flight finishes before we read, and later ones are refused (see migration 0011)
    db.execute(select(Submission.id).where(Submission.id == sub.id).with_for_update()).all()
    if is_finalized(db, sub.id):
        raise FinalizationError("already_finalized", "This result is already finalized.")
    r = readiness(db, sub.id, ctx)
    if not r.ready:
        raise FinalizationError("not_ready", "Not every answer is graded yet.", r.blockers)
    if r.not_attempted and not confirm_not_attempted:
        raise FinalizationError(
            "not_attempted_unconfirmed",
            "Some questions have no answer box and would count as not attempted (0 marks). Confirm to finalize.",
            [{"code": "not_attempted", "message": f"Question {q['label']} has no answer box."} for q in r.not_attempted],
        )
    assert r.sheet is not None
    attempts = attempts_from(live_regions(db, sub.id))
    evals = current_evaluations(db, sub.id, ctx.rubric_version.id)
    att, refs, verdicts = _frozen_inputs(attempts, evals)
    n = len(snapshots_of(db, sub.id)) + 1
    snap = ResultSnapshot(
        submission_id=sub.id,
        exam_id=sub.exam_id,
        snapshot_no=n,
        paper_version_id=ctx.paper_version.id,
        rubric_version_id=ctx.rubric_version.id,
        paper_version_no=ctx.paper_version.version_no,
        rubric_version_no=ctx.rubric_version.version_no,
        score_computer_version=r.sheet.version,
        policy=ctx.rubric_version.policy,
        attempts=att,
        evaluation_refs=refs,
        inputs_sha256=inputs_digest(att, verdicts, [x["evaluation_id"] for x in refs]),
        total=r.sheet.total,
        max_total=r.sheet.max_total,
        complete=r.sheet.complete,
        sheet=sheet_json(r.sheet),
        flags=sorted(r.sheet.flags),
        created_by=actor_id,
    )
    db.add(snap)
    db.flush()
    db.add(FinalizationEvent(submission_id=sub.id, action="FINALIZED", snapshot_id=snap.id, actor_id=actor_id))
    db.flush()
    return snap


def reopen(db: Session, sub: Submission, actor_id: uuid.UUID, reason: str) -> FinalizationEvent:
    """Make a finalized booklet editable again. The snapshot stays; the next finalize adds a new one."""
    db.execute(select(Submission.id).where(Submission.id == sub.id).with_for_update()).all()
    why = reason.strip()
    if len(why) < MIN_REOPEN_REASON:
        raise FinalizationError(
            "reason_required", f"Say why this result is being reopened (at least {MIN_REOPEN_REASON} characters)."
        )
    snap = current_snapshot(db, sub.id)
    if snap is None:
        raise FinalizationError("not_finalized", "This result is not finalized.")
    ev = FinalizationEvent(submission_id=sub.id, action="REOPENED", snapshot_id=snap.id, reason=why, actor_id=actor_id)
    db.add(ev)
    db.flush()
    return ev


# ------------------------------------------------------------------------------------------------------------ verify


def _m(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _node_view(n: dict[str, Any]) -> tuple[Decimal, Decimal, str, int | None, tuple[str, ...]]:
    return (Decimal(n["marks"]), Decimal(n["max_marks"]), n["status"], n["counted_attempt"], tuple(n["flags"]))


def verify_snapshot(db: Session, snap: ResultSnapshot) -> list[str]:
    """Recompute `snap` from the raw records. Returns every difference found; an empty list means it reproduces exactly."""
    problems: list[str] = []
    rv = db.get(RubricVersion, snap.rubric_version_id)
    pv = db.get(PaperVersion, snap.paper_version_id)
    if rv is None or pv is None:
        return ["the rubric or paper version the snapshot points at no longer exists"]
    if rv.policy != snap.policy:
        problems.append("policy: the snapshot's copy differs from the approved rubric version's policy")
    if (rv.version_no, pv.version_no) != (snap.rubric_version_no, snap.paper_version_no):
        problems.append("version numbers: the snapshot's rubric/paper version numbers differ from the versions it points at")
    if snap.score_computer_version != SCORE_COMPUTER_VERSION:
        problems.append(
            f"ScoreComputer: the snapshot was made with {snap.score_computer_version} but this code is {SCORE_COMPUTER_VERSION}; "
            "it cannot be re-verified until that version is available"
        )
        return problems
    ids = [uuid.UUID(r["evaluation_id"]) for r in snap.evaluation_refs]
    rows = {e.id: e for e in db.scalars(select(Evaluation).where(Evaluation.id.in_(ids)))} if ids else {}
    verdict_inputs: list[Verdict] = []
    stored_verdicts: dict[str, dict[str, str]] = {}
    for ref in snap.evaluation_refs:
        e = rows.get(uuid.UUID(ref["evaluation_id"]))
        if e is None:
            problems.append(f"evaluation {ref['evaluation_id']} is missing")
            continue
        if (e.submission_id, e.rubric_version_id, e.qid, e.attempt_no) != (
            snap.submission_id,
            snap.rubric_version_id,
            ref["qid"],
            ref["attempt_no"],
        ):
            problems.append(
                f"evaluation {e.id} does not belong to {ref['qid']} #{ref['attempt_no']} of this booklet and rubric version"
            )
            continue
        stored_verdicts[f"{e.qid}#{e.attempt_no}"] = dict(sorted(e.verdicts.items()))
        verdict_inputs.extend(Verdict(e.qid, e.attempt_no, c, lv) for c, lv in e.verdicts.items())
    digest = inputs_digest(snap.attempts, stored_verdicts, [r["evaluation_id"] for r in snap.evaluation_refs])
    if digest != snap.inputs_sha256:
        problems.append("inputs: the digest of the raw attempts and verdicts differs from the one stored in the snapshot")
    attempts = [Attempt(a["qid"], a["attempt_no"], a["crossed_out"]) for a in snap.attempts]
    sheet = compute(
        Paper.model_validate(pv.document),
        Rubric.model_validate(rv.document),
        Policy.model_validate(rv.policy),
        attempts,
        verdict_inputs,
    )
    if sheet.total != snap.total:
        problems.append(f"total: frozen {_m(snap.total)}, recomputed {_m(sheet.total)}")
    if sheet.max_total != snap.max_total:
        problems.append(f"max_total: frozen {_m(snap.max_total)}, recomputed {_m(sheet.max_total)}")
    if sheet.complete != snap.complete:
        problems.append(f"complete: frozen {snap.complete}, recomputed {sheet.complete}")
    if sorted(sheet.flags) != sorted(snap.flags):
        problems.append(f"flags: frozen {sorted(snap.flags)}, recomputed {sorted(sheet.flags)}")
    fresh = sheet_json(sheet)
    for qid in sorted(set(fresh) | set(snap.sheet)):
        if qid not in snap.sheet or qid not in fresh:
            problems.append(f"question {qid}: present only in the {'recomputed' if qid in fresh else 'frozen'} result")
        elif _node_view(fresh[qid]) != _node_view(snap.sheet[qid]):
            problems.append(f"question {qid}: frozen {snap.sheet[qid]}, recomputed {fresh[qid]}")
    return problems


@dataclass
class VerifyReport:
    checked: int = 0
    failures: dict[str, list[str]] = field(default_factory=dict)  # "booklet/snapshot no" -> differences

    @property
    def ok(self) -> bool:
        return not self.failures


def verify_all(db: Session, exam_id: uuid.UUID | None = None, submission_id: uuid.UUID | None = None) -> VerifyReport:
    q = select(ResultSnapshot).order_by(ResultSnapshot.seq)
    if exam_id is not None:
        q = q.where(ResultSnapshot.exam_id == exam_id)
    if submission_id is not None:
        q = q.where(ResultSnapshot.submission_id == submission_id)
    report = VerifyReport()
    for snap in db.scalars(q):
        report.checked += 1
        problems = verify_snapshot(db, snap)
        if problems:
            report.failures[f"{snap.submission_id}#{snap.snapshot_no}"] = problems
    return report
