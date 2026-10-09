"""Grading documents (D25 a-c): question-paper structure, rubric, and marking policy, with validation.

Pure: no I/O. The paper and the rubric are stored as versioned JSON documents; once a version is APPROVED it is
immutable (enforced in the database, I5/I8). Validation returns every problem at once, each with a path and a message
an examiner can act on, so the editors can show all of them.

Marks are `Decimal` everywhere (never float). Awarding marks happens only in `grademind_core.scoring` (ScoreComputer);
this module only describes structure and maxima.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"
MAX_DEPTH = 4


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------------------------- question paper


class PaperNode(_Strict):
    """A question or sub-question. Leaves carry `max_marks`; a parent's maximum is derived from its children.
    `choose` = internal choice (OR): only `choose` of the children count (e.g. "Answer 5(a) OR 5(b)" -> choose=1)."""

    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1, max_length=40)
    text: str = Field(default="", max_length=4000)
    max_marks: Decimal | None = Field(default=None, gt=0, max_digits=6, decimal_places=2)
    choose: int | None = Field(default=None, ge=1)
    children: tuple[PaperNode, ...] = ()


class Paper(_Strict):
    total_marks: Decimal = Field(gt=0, max_digits=7, decimal_places=2)
    questions: tuple[PaperNode, ...] = Field(min_length=1)


# ---------------------------------------------------------------------------------------------------------- rubric


class Level(_Strict):
    """A named verdict level, e.g. "Full" / "Partial: method correct" / "None". `definition` tells the examiner when
    it applies; it is required for every level strictly between zero and the criterion maximum, and for negative levels."""

    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    marks: Decimal = Field(max_digits=6, decimal_places=2)
    definition: str = Field(default="", max_length=2000)


class Criterion(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    levels: tuple[Level, ...]


class QuestionRubric(_Strict):
    qid: str = Field(pattern=ID_PATTERN)
    criteria: tuple[Criterion, ...]


class Rubric(_Strict):
    questions: tuple[QuestionRubric, ...]


# ---------------------------------------------------------------------------------------------------------- policy


class RoundingMode(StrEnum):
    NONE = "none"
    HALF_UP = "half_up"
    UP = "up"
    DOWN = "down"


class Policy(_Strict):
    """Exam marking policy (stored on the exam). Defaults follow the spec: the last non-crossed attempt counts."""

    or_selection: Literal["best", "first_attempted"] = "best"
    multiple_attempts: Literal["last", "first", "best"] = "last"
    rounding_mode: RoundingMode = RoundingMode.NONE
    rounding_step: Decimal = Field(default=Decimal("0.5"), gt=0, max_digits=4, decimal_places=2)
    rounding_scope: Literal["question", "total"] = "total"
    negative_marking: bool = False
    negative_floor: Literal["question", "total"] = "total"


# ------------------------------------------------------------------------------------------------------ validation


@dataclass(frozen=True)
class Issue:
    path: str  # e.g. "questions/q1/q1a" or "rubric/q1a/c1/partial"
    code: str  # stable, machine-readable
    message: str  # for the examiner


def node_max(node: PaperNode) -> Decimal:
    """Maximum marks of a node: its own for a leaf; the sum of its children; for an OR group, `choose` x the
    (validated equal) child maximum."""
    if not node.children:
        return node.max_marks if node.max_marks is not None else Decimal(0)
    child_max = [node_max(c) for c in node.children]
    if node.choose is not None:
        return sum(sorted(child_max, reverse=True)[: node.choose], Decimal(0))
    return sum(child_max, Decimal(0))


def iter_nodes(nodes: tuple[PaperNode, ...], prefix: str = "questions") -> list[tuple[str, PaperNode, int]]:
    out: list[tuple[str, PaperNode, int]] = []

    def walk(ns: tuple[PaperNode, ...], path: str, depth: int) -> None:
        for n in ns:
            p = f"{path}/{n.id}"
            out.append((p, n, depth))
            walk(n.children, p, depth + 1)

    walk(nodes, prefix, 1)
    return out


def leaves(paper: Paper) -> list[PaperNode]:
    return [n for _, n, _ in iter_nodes(paper.questions) if not n.children]


def paper_max(paper: Paper) -> Decimal:
    return sum((node_max(q) for q in paper.questions), Decimal(0))


def validate_paper(paper: Paper, exam_total: Decimal | None = None) -> list[Issue]:
    issues: list[Issue] = []
    seen: set[str] = set()
    for path, n, depth in iter_nodes(paper.questions):
        if n.id in seen:
            issues.append(Issue(path, "duplicate_id", f"The id '{n.id}' is used more than once. Ids must be unique."))
        seen.add(n.id)
        if depth > MAX_DEPTH:
            issues.append(Issue(path, "too_deep", f"Questions can be nested at most {MAX_DEPTH} levels deep."))
        if n.children:
            if n.max_marks is not None:
                issues.append(
                    Issue(
                        path,
                        "parent_has_marks",
                        f"'{n.label}' has sub-questions, so its marks come from them. Remove its own marks.",
                    )
                )
            if n.choose is not None:
                if n.choose >= len(n.children):
                    issues.append(
                        Issue(
                            path, "choose_too_large", f"'{n.label}': 'answer {n.choose}' needs more than {n.choose} alternatives."
                        )
                    )
                if len({node_max(c) for c in n.children}) > 1:
                    issues.append(
                        Issue(
                            path, "unequal_alternatives", f"'{n.label}': the alternatives of an OR choice must carry equal marks."
                        )
                    )
        else:
            if n.max_marks is None:
                issues.append(Issue(path, "leaf_without_marks", f"'{n.label}' needs its maximum marks."))
            if n.choose is not None:
                issues.append(Issue(path, "choose_on_leaf", f"'{n.label}' has no sub-questions to choose from."))
    computed = paper_max(paper)
    if computed != paper.total_marks:
        issues.append(
            Issue(
                "questions",
                "total_mismatch",
                f"The questions add up to {computed} marks, but the paper total is {paper.total_marks}.",
            )
        )
    if exam_total is not None and paper.total_marks != exam_total:
        issues.append(
            Issue(
                "total_marks",
                "exam_total_mismatch",
                f"The paper total ({paper.total_marks}) differs from the exam total ({exam_total}).",
            )
        )
    return issues


def validate_rubric(rubric: Rubric, paper: Paper, policy: Policy) -> list[Issue]:
    issues: list[Issue] = []
    leaf_by_id = {n.id: n for n in leaves(paper)}
    all_ids = {n.id for _, n, _ in iter_nodes(paper.questions)}
    covered: set[str] = set()
    for qr in rubric.questions:
        base = f"rubric/{qr.qid}"
        if qr.qid in covered:
            issues.append(Issue(base, "duplicate_question", f"Question '{qr.qid}' has more than one rubric."))
            continue
        covered.add(qr.qid)
        leaf = leaf_by_id.get(qr.qid)
        if leaf is None:
            code = "not_a_leaf" if qr.qid in all_ids else "unknown_question"
            issues.append(
                Issue(
                    base, code, f"'{qr.qid}' is not a question that is marked directly (it has sub-questions or does not exist)."
                )
            )
            continue
        if not qr.criteria:
            issues.append(Issue(base, "no_criteria", f"Question {leaf.label} needs at least one criterion."))
            continue
        crit_ids: set[str] = set()
        top_sum = Decimal(0)
        for c in qr.criteria:
            cpath = f"{base}/{c.id}"
            if c.id in crit_ids:
                issues.append(
                    Issue(cpath, "duplicate_criterion", f"Criterion id '{c.id}' is used twice in question {leaf.label}.")
                )
            crit_ids.add(c.id)
            if len(c.levels) < 2:
                issues.append(
                    Issue(cpath, "too_few_levels", f"'{c.name}' needs at least two levels (for example Full and None).")
                )
                continue
            level_ids: set[str] = set()
            for a, b in zip(c.levels, c.levels[1:], strict=False):
                if b.marks <= a.marks:
                    issues.append(
                        Issue(
                            f"{cpath}/{b.id}",
                            "not_monotonic",
                            f"'{c.name}': list levels from lowest to highest marks, each higher than the one before.",
                        )
                    )
            top = c.levels[-1].marks
            low = c.levels[0].marks
            top_sum += top
            if top <= 0:
                issues.append(Issue(cpath, "top_not_positive", f"'{c.name}': the highest level must award marks."))
            if low > 0:
                issues.append(
                    Issue(
                        cpath,
                        "no_zero_level",
                        f"'{c.name}': the lowest level must award 0 marks (or less, with negative marking).",
                    )
                )
            if low < 0 and not policy.negative_marking:
                issues.append(
                    Issue(
                        cpath,
                        "negative_not_allowed",
                        f"'{c.name}' has negative marks, but negative marking is off for this exam.",
                    )
                )
            for lv in c.levels:
                lpath = f"{cpath}/{lv.id}"
                if lv.id in level_ids:
                    issues.append(Issue(lpath, "duplicate_level", f"Level id '{lv.id}' is used twice in '{c.name}'."))
                level_ids.add(lv.id)
                partial = Decimal(0) < lv.marks < top
                if (partial or lv.marks < 0) and not lv.definition.strip():
                    issues.append(
                        Issue(
                            lpath,
                            "definition_required",
                            f"'{c.name}' / '{lv.name}': say when this level applies (a definition is required).",
                        )
                    )
        if leaf.max_marks is not None and top_sum != leaf.max_marks:
            issues.append(
                Issue(
                    base,
                    "max_mismatch",
                    f"Question {leaf.label}: the criteria add up to {top_sum} marks, but the question is worth {leaf.max_marks}.",
                )
            )
    for qid, leaf in leaf_by_id.items():
        if qid not in covered:
            issues.append(Issue(f"rubric/{qid}", "missing_rubric", f"Question {leaf.label} has no rubric yet."))
    return issues
