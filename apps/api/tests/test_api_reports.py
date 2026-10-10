# ruff: noqa: E501 - long literals in assertions
"""4.4 reports through the API: the student's result sheet (PDF) and the exam summary (CSV, PDF). Read back with a real PDF reader.

What must hold: reports exist only for FINALIZED results and say which snapshot they came from; only administrators and teachers get
them; generation is audited with the snapshot ids and a hash of what was produced; the CSV keeps its formula-injection guard; examiner
notes are never printed. Real Postgres + MinIO."""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from fake_ocr import services
from pdfgen import make_pdf
from pdftext import pdf_text, pdf_title
from sqlalchemy import select
from test_api_finalize import audit_actions, finalize, grade, graded, reopen, setup_exam

from grademind_core.db.models import AuditLog
from grademind_core.jobs import run_job
from grademind_worker.stages import PIPELINES

pytestmark = [needs_db, needs_s3]


@pytest.fixture(scope="module")
def ex(world: dict[str, Any]) -> dict[str, Any]:
    return setup_exam(world)


def report(world: dict[str, Any], sub: str, who: str = "teacher") -> Any:
    return world["client"].get(f"/api/submissions/{sub}/report.pdf", headers=login(world, who))


def test_a_result_sheet_exists_only_for_a_finalized_result_and_only_for_staff(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "RP-1")
    r = report(world, b["sub"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_finalized"
    assert finalize(world, b).status_code == 201
    assert report(world, b["sub"], who="exA").status_code == 403
    assert report(world, b["sub"], who="admin2").status_code == 404
    assert world["client"].get(f"/api/submissions/{b['sub']}/report.pdf").status_code == 401
    assert report(world, b["sub"], who="teacher").status_code == 200
    assert report(world, b["sub"], who="admin").status_code == 200


def test_the_sheet_says_what_it_came_from_and_what_was_awarded(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "RP-2")
    world["client"].put(
        f"/api/submissions/{b['sub']}/evaluations/q1/1",
        json={"verdicts": {"c1": "full"}, "notes": "PRIVATE EXAMINER NOTE do not print"},
        headers=login(world, "exA"),
    )
    snap = finalize(world, b).json()
    r = report(world, b["sub"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"] == 'attachment; filename="result_RP-2_s1.pdf"'
    text = pdf_text(r.content)
    assert pdf_title(r.content) == "Result sheet RP-2"
    # where it came from
    assert (
        snap["id"] in text
        and "Snapshot number: 1" in text
        and "Rubric version: 1" in text
        and "ScoreComputer: score-computer-" in text
    )
    assert "finalized by teacher" in text
    # what was awarded, per question and criterion
    assert "Student reference: RP-2" in text and "TOTAL 2 / 5" in text
    assert "1 2 / 2" in text and "Definition: full (2)" in text  # level names are the rubric's ids in this fixture
    assert "not attempted" in text  # Q2 was never answered
    assert "PRIVATE EXAMINER NOTE" not in text  # examiner notes are not printed
    assert text.count(snap["id"]) >= 2  # in the body and in the footer of the page
    assert "Page 1 of 1" in text
    # the audit row ties the report to the snapshot and to the exact bytes
    with world["sessions"]() as s:
        a = s.scalars(select(AuditLog).where(AuditLog.action == "report.student_pdf", AuditLog.entity_id == b["sub"])).one()
    assert a.details["snapshot_id"] == snap["id"] and a.details["snapshot_no"] == 1
    assert a.details["sha256"] == hashlib.sha256(r.content).hexdigest() and a.details["bytes"] == len(r.content)


def test_after_a_reopen_there_is_no_sheet_until_the_next_snapshot_and_it_names_that_one(
    world: dict[str, Any], ex: dict[str, Any]
) -> None:
    b = graded(world, ex, "RP-3", q1="part")
    first = finalize(world, b).json()
    assert "TOTAL 1 / 5" in pdf_text(report(world, b["sub"]).content)
    assert reopen(world, b, "re-marked after moderation").status_code == 200
    assert report(world, b["sub"]).status_code == 409  # open again: no sheet from stale numbers
    assert grade(world, b, "q1", 1, {"c1": "full"}).status_code == 200
    second = finalize(world, b).json()
    text = pdf_text(report(world, b["sub"]).content)
    assert second["id"] != first["id"] and second["id"] in text and first["id"] not in text
    assert "Snapshot number: 2" in text and "TOTAL 2 / 5" in text
    assert audit_actions(world, b["sub"])[-1] == "submission.finalize"


def test_the_summary_csv_has_only_finalized_rows_each_naming_its_snapshot(world: dict[str, Any], ex: dict[str, Any]) -> None:
    done = graded(world, ex, "SM-1")
    open_ = graded(world, ex, "SM-2")
    snap = finalize(world, done).json()
    c = world["client"]
    assert c.get(f"/api/exams/{ex['exam']}/summary.csv", headers=login(world, "exA")).status_code == 403
    r = c.get(f"/api/exams/{ex['exam']}/summary.csv", headers=login(world, "teacher"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    mine = [x for x in rows if x["student_ref"] == "SM-1"]
    assert len(mine) == 1 and mine[0]["snapshot_id"] == snap["id"] and mine[0]["snapshot_no"] == "1"
    assert mine[0]["total"] == "2" and mine[0]["max_total"] == "5" and mine[0]["rubric_version"] == "1"
    assert mine[0]["score_computer_version"].startswith("score-computer-")
    assert not [x for x in rows if x["student_ref"] == open_["sub"] or x["student_ref"] == "SM-2"]  # not final: not in the report
    with world["sessions"]() as s:
        a = s.scalars(select(AuditLog).where(AuditLog.action == "report.summary_csv", AuditLog.entity_id == ex["exam"])).all()
    assert a and snap["id"] in a[-1].details["snapshot_ids"] and a[-1].details["left_out"] >= 1


def test_the_summary_pdf_lists_its_snapshots_and_what_it_left_out(world: dict[str, Any], ex: dict[str, Any]) -> None:
    done = graded(world, ex, "SP-1")
    graded(world, ex, "SP-2")  # not finalized
    snap = finalize(world, done).json()
    r = world["client"].get(f"/api/exams/{ex['exam']}/summary.pdf", headers=login(world, "admin"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    text = pdf_text(r.content)
    assert "Exam summary" in text and "Finalize" in text and snap["id"] in text
    assert "Left out because the result is not finalized" in text and "SP-2" in text
    assert "SP-1: snapshot 1 =" in text and "rubric v1" in text
    assert world["client"].get(f"/api/exams/{ex['exam']}/summary.pdf", headers=login(world, "exA")).status_code == 403


def test_csv_cells_that_look_like_formulas_are_made_inert(world: dict[str, Any]) -> None:
    """A paper's question labels are free text. A label such as =HYPERLINK(...) must not become a live formula in a spreadsheet."""
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "Formula", "subject": "S", "total_marks": "2"}, headers=h).json()["id"]
    paper = {"total_marks": "2", "questions": [{"id": "q1", "label": '=HYPERLINK("http://x","y")', "max_marks": "2"}]}
    rubric = {
        "questions": [
            {
                "qid": "q1",
                "criteria": [
                    {
                        "id": "c1",
                        "name": "A",
                        "levels": [{"id": "no", "name": "No", "marks": "0"}, {"id": "yes", "name": "Yes", "marks": "2"}],
                    }
                ],
            }
        ]
    }
    c.put(f"/api/exams/{eid}/paper/draft", json={"document": paper}, headers=h)
    assert c.post(f"/api/exams/{eid}/paper/approve", headers=h).status_code == 200
    assert c.put(f"/api/exams/{eid}/rubric/draft", json={"document": rubric}, headers=h).json()["issues"] == []
    assert c.post(f"/api/exams/{eid}/rubric/approve", headers=h).status_code == 200
    up = c.post(
        f"/api/exams/{eid}/submissions",
        files={"file": ("b.pdf", make_pdf([["x " + uuid.uuid4().hex]]), "application/pdf")},
        data={"student_ref": "F-1"},
        headers=h,
    ).json()
    assert str(run_job(world["sessions"], uuid.UUID(up["job_id"]), PIPELINES, services=services(world["store"]))) == "COMPLETED"
    page = c.get(f"/api/submissions/{up['id']}/workspace", headers=h).json()["pages"][0]["id"]
    assert (
        c.post(
            f"/api/submissions/{up['id']}/regions", json={"page_id": page, "bbox": [0.1, 0.1, 0.9, 0.4], "qid": "q1"}, headers=h
        ).status_code
        == 201
    )
    assert c.put(f"/api/submissions/{up['id']}/evaluations/q1/1", json={"verdicts": {"c1": "yes"}}, headers=h).status_code == 200
    assert c.post(f"/api/submissions/{up['id']}/finalize", json={}, headers=h).status_code == 201
    text = c.get(f"/api/exams/{eid}/summary.csv", headers=h).text
    header = next(csv.reader(io.StringIO(text)))
    assert header[1].startswith("'=HYPERLINK")  # inert: starts with an apostrophe, not "="
    assert not any(cell.startswith("=") for cell in header)
    pdf = c.get(f"/api/exams/{eid}/summary.pdf", headers=h)
    assert pdf.status_code == 200 and "F-1" in pdf_text(pdf.content)
