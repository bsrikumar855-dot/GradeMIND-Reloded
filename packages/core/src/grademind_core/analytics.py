"""Exam analytics (4.3): read-only statistics from the examiners' VERDICTS and the ScoreComputer's stored results.

Nothing here reads OCR text, OCR confidence or anything derived from them (the import contract proves it), and nothing here
awards or changes a mark: it counts and summarises marks that already exist.

Small numbers mislead, so every statistic carries its `n`, and a statistic that would be computed from fewer than `MIN_N` (5)
observations is NOT shown: percentages, means and medians are `None` and `suppressed` is true. The raw counts stay (an
administrator or teacher can see that 3 of 4 booklets scored 2); only the derived numbers that suggest a pattern are held back.

Two populations: `all` (every booklet's current result) and `finalized` (only booklets whose result is frozen, from the snapshot).
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_core.db.models import AnswerRegion, Evaluation, ResultSnapshot, Submission
from grademind_core.evaluation import GradingContext, current_evaluations, latest_score
from grademind_core.grading import PaperNode, iter_nodes
from grademind_core.result_snapshots import current_snapshot
from grademind_core.scoring import NodeStatus

MIN_N = 5
Scope = Literal["all", "finalized"]
TWO = Decimal("0.01")


@dataclass
class BookletData:
    """One booklet's inputs to the statistics, whichever population it comes from."""

    submission_id: uuid.UUID
    finalized: bool
    complete: bool
    has_answers: bool  # at least one answer box was drawn
    sheet: dict[str, dict[str, Any]]  # question id -> {marks, max_marks, status, counted_attempt, flags}
    verdicts: dict[tuple[str, int], dict[str, str]]  # (question, attempt) -> criterion -> level (current evaluations)


@dataclass
class EvalFact:
    qid: str
    is_override: bool
    seconds_to_first_grade: float | None = None  # only on the first evaluation of an attempt


@dataclass
class AnalyticsInput:
    booklets: list[BookletData]
    evaluations: list[EvalFact] = field(default_factory=list)
    grade_seconds: list[float] = field(default_factory=list)


def _ok(n: int) -> bool:
    return n >= MIN_N


def _share(count: int, n: int) -> str | None:
    """count/n as a decimal string with 4 places, or None when n is too small to quote a percentage."""
    return str((Decimal(count) / Decimal(n)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) if _ok(n) and n else None


def _money(d: Decimal) -> str:
    return format(d.quantize(TWO, rounding=ROUND_HALF_UP).normalize(), "f")


def _leaf_nodes(ctx: GradingContext) -> list[tuple[PaperNode, str]]:
    labels: dict[str, str] = {}

    def walk(nodes: tuple[PaperNode, ...], prefix: str) -> None:
        for n in nodes:
            label = f"{prefix}{n.label}"
            labels[n.id] = label
            walk(n.children, label)

    walk(ctx.paper.questions, "")
    return [(n, labels[n.id]) for _, n, _ in iter_nodes(ctx.paper.questions) if not n.children]


def build_report(
    ctx: GradingContext, data: AnalyticsInput, scope: Scope, rubric_version_no: int, score_computer_version: str
) -> dict[str, Any]:
    """The whole analytics document. Pure: the same input always gives the same output."""
    booklets = data.booklets
    n_total = len(booklets)
    summary = {
        "total": n_total,
        "finalized": sum(1 for b in booklets if b.finalized),
        "not_started": sum(1 for b in booklets if not b.has_answers),
        "in_progress": sum(1 for b in booklets if b.has_answers and not b.complete),
        "complete": sum(1 for b in booklets if b.has_answers and b.complete),
    }
    ungraded_answers = sum(
        1 for b in booklets for node in b.sheet.values() if node["status"] in (NodeStatus.INCOMPLETE.value, "INCOMPLETE")
    )
    rubric_by_q = {qr.qid: qr for qr in ctx.rubric.questions}
    questions: list[dict[str, Any]] = []
    for leaf, label in _leaf_nodes(ctx):
        nodes = [b.sheet[leaf.id] for b in booklets if leaf.id in b.sheet]
        scored = [Decimal(nd["marks"]) for nd in nodes if nd["status"] == NodeStatus.SCORED.value]
        not_attempted = sum(1 for nd in nodes if nd["status"] == NodeStatus.NOT_ATTEMPTED.value)
        incomplete = sum(1 for nd in nodes if nd["status"] == NodeStatus.INCOMPLETE.value)
        n = len(scored)
        dist = Counter(scored)
        distribution = [{"marks": format(m.normalize(), "f"), "count": c, "share": _share(c, n)} for m, c in sorted(dist.items())]
        criteria: list[dict[str, Any]] = []
        qr = rubric_by_q.get(leaf.id)
        for crit in qr.criteria if qr else ():
            chosen: Counter[str] = Counter()
            for b in booklets:
                nd = b.sheet.get(leaf.id)
                if nd is None or nd["counted_attempt"] is None:
                    continue
                att = nd["counted_attempt"]
                if nd["status"] not in (NodeStatus.SCORED.value, NodeStatus.INCOMPLETE.value):
                    continue
                lvl = b.verdicts.get((leaf.id, att), {}).get(crit.id)
                if lvl is not None:
                    chosen[lvl] += 1
            cn = sum(chosen.values())
            criteria.append(
                {
                    "id": crit.id,
                    "name": crit.name,
                    "n": cn,
                    "suppressed": not _ok(cn),
                    "levels": [
                        {
                            "id": lv.id,
                            "name": lv.name,
                            "marks": format(lv.marks.normalize(), "f"),
                            "count": chosen.get(lv.id, 0),
                            "share": _share(chosen.get(lv.id, 0), cn),
                        }
                        for lv in crit.levels
                    ],
                }
            )
        questions.append(
            {
                "qid": leaf.id,
                "label": label,
                "max_marks": format((leaf.max_marks or Decimal(0)).normalize(), "f"),
                "n": n,
                "suppressed": not _ok(n),
                "mean": _money(statistics.mean(scored)) if _ok(n) else None,
                "median": _money(statistics.median(scored)) if _ok(n) else None,
                "not_attempted": not_attempted,
                "incomplete": incomplete,
                "distribution": distribution,
                "criteria": criteria,
            }
        )
    saves = len(data.evaluations)
    overrides = sum(1 for e in data.evaluations if e.is_override)
    secs = [s for s in data.grade_seconds if s >= 0]
    return {
        "scope": scope,
        "min_n": MIN_N,
        "rubric_version_no": rubric_version_no,
        "score_computer_version": score_computer_version,
        "booklets": summary,
        "ungraded": {
            "mapped_but_ungraded_answers": ungraded_answers,
            "booklets_with_ungraded": sum(1 for b in booklets if any(nd["status"] == "INCOMPLETE" for nd in b.sheet.values())),
        },
        "questions": questions,
        "overrides": {
            "grades_saved": saves,
            "overrides": overrides,
            "rate": _share(overrides, saves),
            "suppressed": not _ok(saves),
        },
        "time_to_grade": {
            "n": len(secs),
            "suppressed": not _ok(len(secs)),
            "median_seconds": round(statistics.median(secs)) if _ok(len(secs)) else None,
            "note": (
                "Elapsed time between drawing an answer box and its first grade. "
                "It includes time the grader was away: it is not working time."
            ),
        },
        "definitions": [
            "Marks come from the ScoreComputer's stored results; criterion counts come from the examiners' current verdicts.",
            f"A statistic from fewer than {MIN_N} observations is not shown (percentages, means, medians); counts and n stay.",
            "Nothing here uses machine-read text or its confidence.",
        ],
    }


# ------------------------------------------------------------------------------------------------------------- loading


def _booklet(db: Session, sub: Submission, ctx: GradingContext) -> BookletData | None:
    snap: ResultSnapshot | None = current_snapshot(db, sub.id)
    regions = list(
        db.scalars(select(AnswerRegion).where(AnswerRegion.submission_id == sub.id, AnswerRegion.deleted_at.is_(None)))
    )
    if snap is not None and snap.rubric_version_id == ctx.rubric_version.id:
        ids = {uuid.UUID(r["evaluation_id"]) for r in snap.evaluation_refs}
        rows = {e.id: e for e in db.scalars(select(Evaluation).where(Evaluation.id.in_(ids)))} if ids else {}
        verdicts = {(r["qid"], r["attempt_no"]): dict(rows[uuid.UUID(r["evaluation_id"])].verdicts) for r in snap.evaluation_refs}
        return BookletData(sub.id, True, snap.complete, bool(snap.attempts), dict(snap.sheet), verdicts)
    res = latest_score(db, sub.id, ctx.rubric_version.id)
    if res is None:
        return BookletData(sub.id, False, False, bool(regions), {}, {})
    cur = {k: dict(e.verdicts) for k, e in current_evaluations(db, sub.id, ctx.rubric_version.id).items()}
    return BookletData(sub.id, False, res.complete, bool(regions), dict(res.sheet), cur)


def load(db: Session, exam_id: uuid.UUID, ctx: GradingContext, scope: Scope) -> AnalyticsInput:
    subs = list(
        db.scalars(select(Submission).where(Submission.exam_id == exam_id).order_by(Submission.created_at, Submission.id))
    )
    booklets = [b for s in subs if (b := _booklet(db, s, ctx)) is not None]
    if scope == "finalized":
        booklets = [b for b in booklets if b.finalized]
    keep = {b.submission_id for b in booklets}
    evals = (
        [
            e
            for e in db.scalars(
                select(Evaluation)
                .where(Evaluation.rubric_version_id == ctx.rubric_version.id, Evaluation.submission_id.in_(keep))
                .order_by(Evaluation.seq)
            )
        ]
        if keep
        else []
    )
    first_region: dict[tuple[uuid.UUID, str, int], datetime] = {}
    if keep:
        for r in db.scalars(select(AnswerRegion).where(AnswerRegion.submission_id.in_(keep))):
            k = (r.submission_id, r.qid, r.attempt_no)
            if k not in first_region or r.created_at < first_region[k]:
                first_region[k] = r.created_at
    facts: list[EvalFact] = []
    seconds: list[float] = []
    seen: set[tuple[uuid.UUID, str, int]] = set()
    for e in evals:
        k = (e.submission_id, e.qid, e.attempt_no)
        facts.append(EvalFact(e.qid, e.is_override))
        if k not in seen:
            seen.add(k)
            if k in first_region:
                seconds.append((e.created_at - first_region[k]).total_seconds())
    return AnalyticsInput(booklets, facts, seconds)


def exam_analytics(db: Session, exam_id: uuid.UUID, ctx: GradingContext, scope: Scope = "all") -> dict[str, Any]:
    from grademind_core.scoring import VERSION

    return build_report(ctx, load(db, exam_id, ctx, scope), scope, ctx.rubric_version.version_no, VERSION)
