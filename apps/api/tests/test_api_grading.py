"""Grading workspace through the API (D25 e/f/g): regions, attempts, verdicts -> ScoreComputer, overrides, append-only
records, score sheets written with the evaluation, totals + CSV, exam audit trail. Real Postgres + MinIO + the real
ingest pipeline (run in-process)."""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from pdfgen import make_pdf
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from grademind_api.routes.grading import _cell
from grademind_core.db.models import ScoreResult
from grademind_core.jobs import run_job
from grademind_worker.stages import PIPELINES

pytestmark = [needs_db, needs_s3]


def lv(i: str, m: str, d: str = "") -> dict[str, str]:
    return {"id": i, "name": i, "marks": m, "definition": d}


PAPER = {
    "total_marks": "5",
    "questions": [
        {"id": "q1", "label": "1", "max_marks": "2"},
        {
            "id": "q2",
            "label": "2",
            "choose": 1,
            "children": [{"id": "q2a", "label": "(a)", "max_marks": "3"}, {"id": "q2b", "label": "(b)", "max_marks": "3"}],
        },
    ],
}
RUBRIC = {
    "questions": [
        {
            "qid": "q1",
            "criteria": [
                {"id": "c1", "name": "Definition", "levels": [lv("none", "0"), lv("part", "1", "half"), lv("full", "2")]}
            ],
        },
        {"qid": "q2a", "criteria": [{"id": "c1", "name": "A", "levels": [lv("none", "0"), lv("full", "3")]}]},
        {
            "qid": "q2b",
            "criteria": [
                {"id": "m", "name": "Method", "levels": [lv("none", "0"), lv("full", "2")]},
                {"id": "u", "name": "Units", "levels": [lv("none", "0"), lv("ok", "1")]},
            ],
        },
    ]
}


@pytest.fixture(scope="module")
def g(world: dict[str, Any]) -> dict[str, Any]:
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "Grading", "subject": "EVS", "total_marks": "5"}, headers=h).json()["id"]
    c.post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    up = c.post(
        f"/api/exams/{eid}/submissions",
        files={"file": ("b.pdf", make_pdf([["Q1 answer " + uuid.uuid4().hex], ["Q2 answer"]]), "application/pdf")},
        data={"student_ref": "S-007"},
        headers=h,
    ).json()
    sid = up["id"]
    assert c.get(f"/api/submissions/{sid}/workspace", headers=h).json()["error"]["code"] == "rubric_not_approved"
    c.put(f"/api/exams/{eid}/paper/draft", json={"document": PAPER}, headers=h)
    assert c.post(f"/api/exams/{eid}/paper/approve", headers=h).status_code == 200
    r = c.put(f"/api/exams/{eid}/rubric/draft", json={"document": RUBRIC}, headers=h)
    assert r.json()["issues"] == []
    assert c.post(f"/api/exams/{eid}/rubric/approve", headers=h).status_code == 200
    assert str(run_job(world["sessions"], uuid.UUID(up["job_id"]), PIPELINES, services={"store": world["store"]})) == "COMPLETED"
    ws = c.get(f"/api/submissions/{sid}/workspace", headers=login(world, "exA")).json()
    return {"exam": eid, "sub": sid, "pages": [pg["id"] for pg in ws["pages"]]}


def region(world: dict[str, Any], g: dict[str, Any], qid: str, page: int = 0, who: str = "exA", **kw: Any) -> Any:
    body = {"page_id": g["pages"][page], "bbox": [0.1, 0.1, 0.9, 0.5], "qid": qid, **kw}
    return world["client"].post(f"/api/submissions/{g['sub']}/regions", json=body, headers=login(world, who))


def grade(
    world: dict[str, Any], g: dict[str, Any], qid: str, att: int, verdicts: dict[str, str], who: str = "exA", **kw: Any
) -> Any:
    return world["client"].put(
        f"/api/submissions/{g['sub']}/evaluations/{qid}/{att}", json={"verdicts": verdicts, **kw}, headers=login(world, who)
    )


def scores_count(world: dict[str, Any], g: dict[str, Any]) -> int:
    with world["sessions"]() as s:
        return int(
            s.scalar(select(func.count()).select_from(ScoreResult).where(ScoreResult.submission_id == uuid.UUID(g["sub"]))) or 0
        )


def test_workspace_contents(world: dict[str, Any], g: dict[str, Any]) -> None:
    ws = world["client"].get(f"/api/submissions/{g['sub']}/workspace", headers=login(world, "exA")).json()
    assert len(ws["pages"]) == 2 and ws["paper"]["total_marks"] == "5" and ws["score"]["total"] == "0"
    assert ws["score"]["nodes"]["q1"]["status"] == "NOT_ATTEMPTED" and ws["student_ref"] == "S-007"
    for who in ("exB", "admin2"):
        assert world["client"].get(f"/api/submissions/{g['sub']}/workspace", headers=login(world, who)).status_code == 404


def test_region_validation(world: dict[str, Any], g: dict[str, Any]) -> None:
    assert region(world, g, "q2").json()["error"]["code"] == "invalid_question"  # q2 is an OR group, not a leaf
    assert region(world, g, "q1", bbox=[0.5, 0.1, 0.4, 0.2]).status_code == 422
    r = world["client"].post(
        f"/api/submissions/{g['sub']}/regions",
        json={"page_id": str(uuid.uuid4()), "bbox": [0, 0, 1, 1], "qid": "q1"},
        headers=login(world, "exA"),
    )
    assert r.json()["error"]["code"] == "invalid_page"
    assert region(world, g, "q1", who="exB").status_code == 404


def test_grading_flow_attempts_crossing_and_or_choice(world: dict[str, Any], g: dict[str, Any]) -> None:
    assert grade(world, g, "q1", 1, {"c1": "full"}).json()["error"]["code"] == "no_answer"
    r1 = region(world, g, "q1")
    assert r1.status_code == 201 and r1.json()["attempt_no"] == 1
    assert region(world, g, "q1", page=1).json()["attempt_no"] == 1  # continuation on the next page: same attempt
    assert grade(world, g, "q1", 1, {"c1": "bogus"}).json()["error"]["code"] == "invalid_verdict"
    before = scores_count(world, g)
    s = grade(world, g, "q1", 1, {"c1": "full"}, notes="clear definition", evidence_region_id=r1.json()["id"])
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["evaluation"]["marks"] == "2" and body["score"]["total"] == "2" and body["score"]["complete"]
    assert scores_count(world, g) == before + 1  # the sheet is written with the evaluation

    r2 = region(world, g, "q1", page=1, new_attempt=True).json()
    assert r2["attempt_no"] == 2
    s = grade(world, g, "q1", 2, {"c1": "part"}).json()
    assert s["score"]["nodes"]["q1"]["counted_attempt"] == 2 and s["score"]["total"] == "1"  # policy: last attempt counts
    assert "MULTIPLE_ATTEMPTS" in s["score"]["flags"]
    c = world["client"].post(
        f"/api/submissions/{g['sub']}/regions/{r2['id']}/crossed", json={"crossed_out": True}, headers=login(world, "exA")
    )
    assert c.json()["crossed_out"]
    ws = world["client"].get(f"/api/submissions/{g['sub']}/workspace", headers=login(world, "exA")).json()
    assert ws["score"]["nodes"]["q1"]["counted_attempt"] == 1 and ws["score"]["total"] == "2"  # crossed-out attempt ignored

    region(world, g, "q2a")
    region(world, g, "q2b", page=1)
    grade(world, g, "q2a", 1, {"c1": "full"})
    s = grade(world, g, "q2b", 1, {"m": "full"}).json()  # Units not graded yet
    q2b = s["score"]["nodes"]["q2b"]
    assert not s["score"]["complete"] and q2b["status"] == "EXCLUDED_BY_CHOICE" and q2b["flags"] == ["INCOMPLETE"]
    s = grade(world, g, "q2b", 1, {"m": "full", "u": "ok"}).json()
    assert s["score"]["total"] == "5" and "OR_EXTRA_ATTEMPTED" in s["score"]["flags"]  # q2 counts the better alternative (3)


def test_overrides_need_a_reason_and_keep_history(world: dict[str, Any], g: dict[str, Any]) -> None:
    r = grade(world, g, "q1", 1, {"c1": "part"}, who="teacher")
    assert r.status_code == 422 and r.json()["error"]["code"] == "override_reason_required"
    r = grade(world, g, "q1", 1, {"c1": "part"}, who="teacher", override_reason="definition omits the abiotic part")
    assert r.status_code == 200 and r.json()["evaluation"]["is_override"] and r.json()["score"]["nodes"]["q1"]["marks"] == "1"
    hist = world["client"].get(f"/api/submissions/{g['sub']}/evaluations/q1/1/history", headers=login(world, "exA")).json()
    assert [h["is_override"] for h in hist] == [False, True]
    assert hist[1]["supersedes_id"] == hist[0]["id"] and hist[0]["marks"] == "2"  # the original stays, unchanged


def test_evaluations_and_score_results_are_append_only(world: dict[str, Any], g: dict[str, Any]) -> None:
    for sql in ("UPDATE evaluations SET marks = 99", "DELETE FROM score_results", "UPDATE score_results SET total = 99"):
        with pytest.raises(DBAPIError, match="append-only"), world["sessions"]() as s, s.begin():
            s.execute(text(sql))


def test_deleting_a_graded_region_flags_the_orphan(world: dict[str, Any], g: dict[str, Any]) -> None:
    c = world["client"]
    rid = region(world, g, "q2a", page=1, new_attempt=True).json()
    ws = c.get(f"/api/submissions/{g['sub']}/workspace", headers=login(world, "exA")).json()
    grade(world, g, "q2a", rid["attempt_no"], {"c1": "none"})
    assert c.delete(f"/api/submissions/{g['sub']}/regions/{rid['id']}", headers=login(world, "exA")).status_code == 204
    ws = c.get(f"/api/submissions/{g['sub']}/workspace", headers=login(world, "exA")).json()
    assert "ORPHANED_EVALUATIONS" in ws["score"]["flags"] and all(r["id"] != rid["id"] for r in ws["regions"])
    assert c.delete(f"/api/submissions/{g['sub']}/regions/{rid['id']}", headers=login(world, "exA")).status_code == 404


def test_totals_and_csv(world: dict[str, Any], g: dict[str, Any]) -> None:
    c = world["client"]
    t = c.get(f"/api/exams/{g['exam']}/totals", headers=login(world, "exA")).json()
    assert [col["label"] for col in t["columns"]] == ["1", "2"] and [col["max"] for col in t["columns"]] == ["2", "3"]
    (row,) = t["rows"]
    assert row["student_ref"] == "S-007" and row["sections"] == {"q1": "1", "q2": "3"} and row["total"] == "4"
    r = c.get(f"/api/exams/{g['exam']}/totals.csv", headers=login(world, "teacher"))
    assert (
        r.status_code == 200
        and r.headers["content-type"].startswith("text/csv")
        and "attachment" in r.headers["content-disposition"]
    )
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:3] == ["student_ref", "1 (/2)", "2 (/3)"] and rows[1][:4] == ["S-007", "1", "3", "4"]
    assert Decimal(rows[1][4]) == Decimal(5)


def test_csv_cells_cannot_inject_formulas() -> None:
    assert _cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _cell("+1") == "'+1" and _cell("@x") == "'@x" and _cell("S-1") == "S-1"
    assert _cell(None) == "" and _cell(Decimal("-1")) == "'-1"


def test_exam_audit_trail(world: dict[str, Any], g: dict[str, Any]) -> None:
    c = world["client"]
    rows = c.get(f"/api/exams/{g['exam']}/audit", headers=login(world, "teacher")).json()
    actions = [r["action"] for r in rows]
    for a in [
        "exam.create",
        "paper.approve",
        "rubric.approve",
        "submission.create",
        "region.create",
        "evaluation.save",
        "evaluation.override",
        "region.delete",
        "totals.export_csv",
    ]:
        assert a in actions, a
    assert actions.index("rubric.approve") < actions.index("evaluation.save")  # chronological
    assert c.get(f"/api/exams/{g['exam']}/audit", headers=login(world, "exA")).status_code == 403
