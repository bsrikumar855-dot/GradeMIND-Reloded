"""ScoreComputer (D25 c). The ONLY place where marks are awarded or added up (spec rule 3; enforced by
scripts/check_single_paths.py). Pure, deterministic, Decimal-only, versioned.

Inputs: an approved paper, an approved rubric, the exam policy, the answer attempts found in the booklet, and the
examiner's verdicts (one level per criterion per attempt). Output: marks per node, the total, and flags.

Rules, in order:
1. An attempt's marks = the sum of the chosen level marks over the question's criteria. A criterion without a
   verdict makes the attempt INCOMPLETE (its marks so far still count provisionally; `complete` is False).
2. Crossed-out attempts never count. Several remaining attempts of one question -> MULTIPLE_ATTEMPTS, and the
   policy picks one: `last` (spec default), `first`, or `best` (ties -> the later attempt).
3. Negative marking: with `negative_floor="question"` a question never goes below 0; otherwise only the total is
   floored at 0.
4. Rounding (`rounding_scope`): each question, or the total, is rounded to `rounding_step` with `rounding_mode`, and
   capped at its maximum so rounding up can never exceed it.
5. A parent = the sum of its children; an OR group (`choose=k`) counts k children: the `best` k, or the first k
   attempted in paper order (`first_attempted`). Attempting more than k -> OR_EXTRA_ATTEMPTED.

Invariants (property-tested): 0 <= total <= max; raising a verdict never lowers the total; the order of attempts and
verdicts does not matter; computing twice gives the same result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import StrEnum

from grademind_core.grading import Paper, PaperNode, Policy, RoundingMode, Rubric, node_max

VERSION = "score-computer-1.0.0"
ZERO = Decimal(0)


class ScoreInputError(ValueError):
    """The verdicts or attempts do not fit the paper/rubric (unknown question, criterion or level)."""


class NodeStatus(StrEnum):
    SCORED = "SCORED"
    INCOMPLETE = "INCOMPLETE"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    EXCLUDED_BY_CHOICE = "EXCLUDED_BY_CHOICE"


@dataclass(frozen=True)
class Attempt:
    qid: str
    attempt_no: int  # 1 = first in the booklet
    crossed_out: bool = False


@dataclass(frozen=True)
class Verdict:
    qid: str
    attempt_no: int
    criterion_id: str
    level_id: str


@dataclass(frozen=True)
class NodeScore:
    qid: str
    marks: Decimal
    max_marks: Decimal
    status: NodeStatus
    counted_attempt: int | None = None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoreSheet:
    version: str
    total: Decimal
    max_total: Decimal
    complete: bool
    nodes: dict[str, NodeScore] = field(default_factory=dict)
    flags: tuple[str, ...] = ()


def _round(x: Decimal, policy: Policy, cap: Decimal) -> Decimal:
    if policy.rounding_mode == RoundingMode.NONE:
        return x
    mode = {RoundingMode.HALF_UP: ROUND_HALF_UP, RoundingMode.UP: ROUND_CEILING, RoundingMode.DOWN: ROUND_FLOOR}[
        policy.rounding_mode
    ]
    step = policy.rounding_step
    rounded = (x / step).quantize(Decimal(1), rounding=mode) * step
    return min(rounded, cap)


@dataclass
class _Ctx:
    policy: Policy
    rubric: dict[str, dict[str, dict[str, Decimal]]]  # qid -> criterion -> level -> marks
    attempts: dict[str, list[Attempt]]
    verdicts: dict[tuple[str, int], dict[str, str]]  # (qid, attempt) -> criterion -> level
    nodes: dict[str, NodeScore]
    flags: set[str]
    complete: bool = True


def _leaf(n: PaperNode, ctx: _Ctx) -> NodeScore:
    mx = node_max(n)
    live = sorted((a for a in ctx.attempts.get(n.id, []) if not a.crossed_out), key=lambda a: a.attempt_no)
    if not live:
        return NodeScore(n.id, ZERO, mx, NodeStatus.NOT_ATTEMPTED)
    criteria = ctx.rubric[n.id]

    def attempt_marks(a: Attempt) -> tuple[Decimal, bool]:
        chosen = ctx.verdicts.get((n.id, a.attempt_no), {})
        marks = sum((criteria[c][lv] for c, lv in chosen.items()), ZERO)
        return marks, len(chosen) == len(criteria)

    flags: tuple[str, ...] = ()
    if len(live) > 1:
        flags = ("MULTIPLE_ATTEMPTS",)
        ctx.flags.add("MULTIPLE_ATTEMPTS")
    policy = ctx.policy
    if policy.multiple_attempts == "first":
        pick = live[0]
    elif policy.multiple_attempts == "best":
        pick = max(reversed(live), key=lambda a: attempt_marks(a)[0])  # reversed: ties go to the later attempt
    else:
        pick = live[-1]
    marks, done = attempt_marks(pick)
    if policy.negative_marking and policy.negative_floor == "question":
        marks = max(marks, ZERO)
    if policy.rounding_scope == "question":
        marks = _round(marks, policy, mx)
    if not done:
        ctx.complete = False
    return NodeScore(n.id, marks, mx, NodeStatus.SCORED if done else NodeStatus.INCOMPLETE, pick.attempt_no, flags)


def _attempted(s: NodeScore) -> bool:
    return s.status in (NodeStatus.SCORED, NodeStatus.INCOMPLETE)


def _node(n: PaperNode, ctx: _Ctx) -> NodeScore:
    if not n.children:
        s = _leaf(n, ctx)
        ctx.nodes[n.id] = s
        return s
    kids = [_node(c, ctx) for c in n.children]
    mx = node_max(n)
    flags: tuple[str, ...] = ()
    if n.choose is None:
        counted = kids
    else:
        tried = [k for k in kids if _attempted(k)]
        if len(tried) > n.choose:
            flags = ("OR_EXTRA_ATTEMPTED",)
            ctx.flags.add("OR_EXTRA_ATTEMPTED")
        if ctx.policy.or_selection == "first_attempted":
            counted = tried[: n.choose]
        else:
            order = {k.qid: i for i, k in enumerate(kids)}
            counted = sorted(tried, key=lambda k: (-k.marks, order[k.qid]))[: n.choose]
        keep = {k.qid for k in counted}
        for k in kids:
            if _attempted(k) and k.qid not in keep:
                # keep the reason visible: an excluded alternative can still be what makes the sheet incomplete
                extra = ("INCOMPLETE",) if k.status == NodeStatus.INCOMPLETE else ()
                ctx.nodes[k.qid] = NodeScore(
                    k.qid, k.marks, k.max_marks, NodeStatus.EXCLUDED_BY_CHOICE, k.counted_attempt, k.flags + extra
                )
    marks = sum((k.marks for k in counted), ZERO)
    status = NodeStatus.SCORED if any(_attempted(k) for k in kids) else NodeStatus.NOT_ATTEMPTED
    if any(k.status == NodeStatus.INCOMPLETE for k in counted):
        status = NodeStatus.INCOMPLETE
    s = NodeScore(n.id, marks, mx, status, None, flags)
    ctx.nodes[n.id] = s
    return s


def compute(paper: Paper, rubric: Rubric, policy: Policy, attempts: list[Attempt], verdicts: list[Verdict]) -> ScoreSheet:
    """Validate the inputs against the paper/rubric, then compute. Raises ScoreInputError on unknown ids, a verdict
    for an attempt that does not exist, or two different verdicts for one criterion of one attempt."""
    table = {qr.qid: {c.id: {lv.id: lv.marks for lv in c.levels} for c in qr.criteria} for qr in rubric.questions}
    by_q: dict[str, list[Attempt]] = {}
    seen_attempts: set[tuple[str, int]] = set()
    for a in attempts:
        if a.qid not in table:
            raise ScoreInputError(f"attempt for unknown question {a.qid!r}")
        if (a.qid, a.attempt_no) in seen_attempts:
            raise ScoreInputError(f"duplicate attempt {a.qid!r} #{a.attempt_no}")
        seen_attempts.add((a.qid, a.attempt_no))
        by_q.setdefault(a.qid, []).append(a)
    chosen: dict[tuple[str, int], dict[str, str]] = {}
    for v in verdicts:
        if (v.qid, v.attempt_no) not in seen_attempts:
            raise ScoreInputError(f"verdict for an attempt that does not exist: {v.qid!r} #{v.attempt_no}")
        levels = table[v.qid].get(v.criterion_id)
        if levels is None or v.level_id not in levels:
            raise ScoreInputError(f"unknown criterion/level {v.criterion_id!r}/{v.level_id!r} for {v.qid!r}")
        slot = chosen.setdefault((v.qid, v.attempt_no), {})
        if slot.get(v.criterion_id, v.level_id) != v.level_id:
            raise ScoreInputError(f"two different verdicts for {v.qid!r} #{v.attempt_no} criterion {v.criterion_id!r}")
        slot[v.criterion_id] = v.level_id
    ctx = _Ctx(policy=policy, rubric=table, attempts=by_q, verdicts=chosen, nodes={}, flags=set())
    tops = [_node(q, ctx) for q in paper.questions]
    max_total = sum((t.max_marks for t in tops), ZERO)
    total = sum((t.marks for t in tops), ZERO)
    if policy.negative_marking:
        total = max(total, ZERO)
    if policy.rounding_scope == "total":
        total = _round(total, policy, max_total)
    return ScoreSheet(VERSION, total, max_total, ctx.complete, dict(sorted(ctx.nodes.items())), tuple(sorted(ctx.flags)))
