"""Result reports (4.4): a student's result sheet and the exam summary, built ONLY from finalized result snapshots.

A report is a view of a frozen snapshot: it never recomputes a mark and never reads a live evaluation or any OCR output.
Every report says which snapshot(s) it came from (id and number), the rubric version, and the ScoreComputer version, so a
printed sheet can always be traced to the record it was made from (and checked with `verify-snapshots`). Examiner notes are
not printed.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_core.db.models import Evaluation, Exam, PaperVersion, ResultSnapshot, RubricVersion, Submission, User
from grademind_core.grading import Paper, PaperNode, Rubric
from grademind_core.pdfwrite import Document
from grademind_core.result_snapshots import current_snapshot

TABLE_W = 72  # characters across the marks table (label column, then the right-aligned marks)
WIDTH = 90  # characters per line of monospaced text at 9 pt on A4
STATUS_TEXT = {
    "SCORED": "",
    "INCOMPLETE": "not fully graded",
    "NOT_ATTEMPTED": "not attempted",
    "EXCLUDED_BY_CHOICE": "alternative, not counted",
}
FLAG_TEXT = {
    "MULTIPLE_ATTEMPTS": "The student answered a question more than once; the exam policy chose which attempt counts.",
    "OR_EXTRA_ATTEMPTED": "More alternatives were answered than the paper asks for; only the allowed number counted.",
}


def _m(value: object) -> str:
    """A mark as a plain decimal string: 2, 1.5, 20."""
    from decimal import Decimal

    d = Decimal(str(value))
    return format(d.normalize(), "f")


@dataclass(frozen=True)
class CriterionLine:
    name: str
    level: str
    marks: str


@dataclass(frozen=True)
class QuestionLine:
    label: str
    depth: int
    marks: str
    max_marks: str
    status: str
    counted_attempt: int | None
    criteria: tuple[CriterionLine, ...] = ()


@dataclass(frozen=True)
class SheetData:
    exam_name: str
    subject: str
    student_ref: str
    snapshot_id: uuid.UUID
    snapshot_no: int
    rubric_version_no: int
    paper_version_no: int
    score_computer_version: str
    finalized_by: str
    finalized_at: datetime
    total: str
    max_total: str
    complete: bool
    flags: tuple[str, ...]
    questions: tuple[QuestionLine, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SummaryRow:
    student_ref: str
    snapshot_id: uuid.UUID
    snapshot_no: int
    rubric_version_no: int
    score_computer_version: str
    total: str
    max_total: str
    complete: bool
    sections: dict[str, str]  # top-level question id -> marks
    flags: tuple[str, ...]


@dataclass(frozen=True)
class SummaryData:
    exam_name: str
    subject: str
    columns: tuple[tuple[str, str, str], ...]  # (top-level id, label, max)
    rows: tuple[SummaryRow, ...]
    not_finalized: tuple[str, ...]  # student references left out because their result is not final


# ------------------------------------------------------------------------------------------------------------ loading


def _walk(nodes: tuple[PaperNode, ...], depth: int, prefix: str) -> list[tuple[PaperNode, int, str]]:
    out: list[tuple[PaperNode, int, str]] = []
    for n in nodes:
        label = f"{prefix} {n.label}".strip() if depth > 0 and not n.label.startswith("(") else f"{prefix}{n.label}"
        out.append((n, depth, label))
        out.extend(_walk(n.children, depth + 1, label))
    return out


def sheet_for(db: Session, sub: Submission, snap: ResultSnapshot) -> SheetData:
    exam = db.get(Exam, sub.exam_id)
    pv, rv = db.get(PaperVersion, snap.paper_version_id), db.get(RubricVersion, snap.rubric_version_id)
    assert exam is not None and pv is not None and rv is not None
    paper, rubric = Paper.model_validate(pv.document), Rubric.model_validate(rv.document)
    ids = [uuid.UUID(r["evaluation_id"]) for r in snap.evaluation_refs]
    evals = {e.id: e for e in db.scalars(select(Evaluation).where(Evaluation.id.in_(ids)))} if ids else {}
    verdicts = {(r["qid"], r["attempt_no"]): evals[uuid.UUID(r["evaluation_id"])].verdicts for r in snap.evaluation_refs}
    crit_by_q = {qr.qid: qr.criteria for qr in rubric.questions}
    lines: list[QuestionLine] = []
    for node, depth, label in _walk(paper.questions, 0, ""):
        nd = snap.sheet.get(node.id)
        if nd is None:
            continue
        crits: list[CriterionLine] = []
        att = nd["counted_attempt"]
        if not node.children and att is not None:
            chosen = verdicts.get((node.id, att), {})
            for c in crit_by_q.get(node.id, ()):
                lv = next((x for x in c.levels if x.id == chosen.get(c.id)), None)
                crits.append(CriterionLine(c.name, lv.name if lv else "(no verdict)", _m(lv.marks) if lv else "-"))
        lines.append(QuestionLine(label, depth, _m(nd["marks"]), _m(nd["max_marks"]), nd["status"], att, tuple(crits)))
    who = db.get(User, snap.created_by)
    return SheetData(
        exam_name=exam.name,
        subject=exam.subject,
        student_ref=sub.student_ref,
        snapshot_id=snap.id,
        snapshot_no=snap.snapshot_no,
        rubric_version_no=snap.rubric_version_no,
        paper_version_no=snap.paper_version_no,
        score_computer_version=snap.score_computer_version,
        finalized_by=who.display_name if who else "?",
        finalized_at=snap.created_at,
        total=_m(snap.total),
        max_total=_m(snap.max_total),
        complete=snap.complete,
        flags=tuple(snap.flags),
        questions=tuple(lines),
    )


def summary_for(db: Session, exam: Exam) -> SummaryData:
    subs = list(
        db.scalars(
            select(Submission).where(Submission.exam_id == exam.id).order_by(Submission.student_ref, Submission.created_at)
        )
    )
    rows: list[SummaryRow] = []
    left_out: list[str] = []
    columns: dict[str, tuple[str, str, str]] = {}
    for sub in subs:
        snap = current_snapshot(db, sub.id)
        if snap is None:
            left_out.append(sub.student_ref)
            continue
        pv = db.get(PaperVersion, snap.paper_version_id)
        assert pv is not None
        for node in Paper.model_validate(pv.document).questions:
            nd = snap.sheet.get(node.id)
            if node.id not in columns and nd is not None:
                columns[node.id] = (node.id, node.label, _m(nd["max_marks"]))
        tops = {n.id for n in Paper.model_validate(pv.document).questions}
        rows.append(
            SummaryRow(
                student_ref=sub.student_ref,
                snapshot_id=snap.id,
                snapshot_no=snap.snapshot_no,
                rubric_version_no=snap.rubric_version_no,
                score_computer_version=snap.score_computer_version,
                total=_m(snap.total),
                max_total=_m(snap.max_total),
                complete=snap.complete,
                sections={k: _m(v["marks"]) for k, v in snap.sheet.items() if k in tops},
                flags=tuple(snap.flags),
            )
        )
    return SummaryData(exam.name, exam.subject, tuple(columns.values()), tuple(rows), tuple(left_out))


# ------------------------------------------------------------------------------------------------------------ rendering


def _stamp(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M UTC")


def student_sheet_document(data: SheetData, generated_at: datetime) -> Document:
    doc = Document(
        title=f"Result sheet {data.student_ref}",
        footer=(
            f"Snapshot {data.snapshot_id} (no. {data.snapshot_no}) | rubric v{data.rubric_version_no} | "
            f"{data.score_computer_version}"
        ),
    )
    doc.heading("Result sheet", 20)
    doc.line(f"{data.exam_name}  -  {data.subject}", "Helvetica", 11, wrap=False)
    doc.blank()
    doc.line(f"Student reference: {data.student_ref}")
    doc.line(f"Result snapshot:   {data.snapshot_id}")
    doc.line(f"Snapshot number:   {data.snapshot_no}   (finalized by {data.finalized_by} on {_stamp(data.finalized_at)})")
    doc.line(f"Rubric version:    {data.rubric_version_no}      Question paper version: {data.paper_version_no}")
    doc.line(f"ScoreComputer:     {data.score_computer_version}")
    doc.line(f"Report made:       {_stamp(generated_at)}")
    doc.rule()
    doc.line(
        f"TOTAL  {data.total} / {data.max_total}" + ("" if data.complete else "   (not every answer fully graded)"),
        "Courier",
        12,
        wrap=False,
    )
    doc.rule()
    doc.line(f"{'Question':<{TABLE_W - 14}}{'Marks':>14}", "Courier", 9, wrap=False)
    for q in data.questions:
        pad = "  " * q.depth
        right = f"{q.marks} / {q.max_marks}"
        doc.line(f"{pad + q.label:<{TABLE_W - 14}}{right:>14}", "Courier", 9, wrap=False)
        remarks = [STATUS_TEXT.get(q.status, q.status.lower())] if STATUS_TEXT.get(q.status, q.status.lower()) else []
        if q.counted_attempt not in (None, 1):
            remarks.append(f"attempt {q.counted_attempt} counted")
        if remarks:
            doc.line(f"{pad}    ({', '.join(remarks)})", "Courier", 8, wrap=False)
        for c in q.criteria:
            doc.line(f"{pad}    - {c.name}: {c.level} ({c.marks})", "Courier", 8.5, indent=0)
    notes = [FLAG_TEXT[f] for f in data.flags if f in FLAG_TEXT]
    if notes:
        doc.rule()
        doc.line("Notes", "Helvetica-Bold", 10, wrap=False)
        for n in notes:
            doc.line(f"- {n}", "Courier", 9)
    doc.rule()
    doc.line(
        "This sheet is a copy of a frozen result record. The marks were awarded by the examiner's verdicts and computed by the "
        "ScoreComputer named above; nothing on this sheet comes from machine-read text.",
        "Courier",
        8,
    )
    return doc


def summary_document(data: SummaryData, generated_at: datetime) -> Document:
    doc = Document(
        title=f"Exam summary {data.exam_name}", footer=f"Exam summary | {data.exam_name} | {len(data.rows)} finalized result(s)"
    )
    doc.heading("Exam summary", 20)
    doc.line(f"{data.exam_name}  -  {data.subject}", "Helvetica", 11, wrap=False)
    doc.line(f"Report made: {_stamp(generated_at)}")
    doc.line(f"Finalized results in this report: {len(data.rows)}")
    if data.not_finalized:
        doc.line(f"Left out because the result is not finalized ({len(data.not_finalized)}): " + ", ".join(data.not_finalized))
    doc.rule()
    head = f"{'Student':<16}{'Snap':>5}{'Total':>12}   Sections"
    doc.line(head, "Courier", 9, wrap=False)
    for r in data.rows:
        secs = "  ".join(f"{label}={r.sections.get(qid, '-')}" for qid, label, _ in data.columns)
        doc.line(f"{r.student_ref:<16}{r.snapshot_no:>5}{r.total + ' / ' + r.max_total:>12}   {secs}", "Courier", 9)
    doc.rule()
    doc.line("Snapshots these figures come from", "Helvetica-Bold", 10, wrap=False)
    for r in data.rows:
        ref = f"{r.student_ref}: snapshot {r.snapshot_no} = {r.snapshot_id}"
        doc.line(f"{ref} (rubric v{r.rubric_version_no}, {r.score_computer_version})", "Courier", 8)
    doc.blank()
    doc.line(
        "Each figure is copied from a frozen result record; nothing was recomputed or taken from machine-read text.", "Courier", 8
    )
    return doc


def summary_csv(data: SummaryData, cell: Callable[[object], str]) -> str:
    """The summary as CSV. `cell` is the formula-injection guard (a cell starting with = + - @ is made inert)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["student_ref"]
        + [cell(f"{label} (/{mx})") for _, label, mx in data.columns]
        + ["total", "max_total", "complete", "flags", "snapshot_no", "snapshot_id", "rubric_version", "score_computer_version"]
    )
    for r in data.rows:
        w.writerow(
            [cell(r.student_ref)]
            + [cell(r.sections.get(qid, "")) for qid, _, _ in data.columns]
            + [cell(r.total), cell(r.max_total), "yes" if r.complete else "no", cell(" ".join(r.flags))]
            + [r.snapshot_no, r.snapshot_id, r.rubric_version_no, cell(r.score_computer_version)]
        )
    return buf.getvalue()


def as_json(sheet: SheetData) -> dict[str, Any]:  # used by tests and by the API's JSON preview
    return {
        "student_ref": sheet.student_ref,
        "snapshot_id": str(sheet.snapshot_id),
        "snapshot_no": sheet.snapshot_no,
        "rubric_version_no": sheet.rubric_version_no,
        "total": sheet.total,
        "max_total": sheet.max_total,
        "questions": [q.label for q in sheet.questions],
    }
