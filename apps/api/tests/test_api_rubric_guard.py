# ruff: noqa: E501 - long literals in assertions
"""5.1 (D30.9): once any grade exists, a new rubric version needs an administrator and a reason; existing grades stay tied to the
version they were given under; finalized results and their snapshots are untouched, and the workspace says which version it shows."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from sqlalchemy import select
from test_api_finalize import RUBRIC, booklet, finalize, grade, graded, region, setup_exam

from grademind_core.db.models import AuditLog, Evaluation, ResultSnapshot
from grademind_core.result_snapshots import verify_all

pytestmark = [needs_db, needs_s3]


@pytest.fixture()
def ex(world: dict[str, Any]) -> dict[str, Any]:
    return setup_exam(world)  # a fresh exam per test: rubric v1 approved, nothing graded


def state(world: dict[str, Any], ex: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = world["client"].get(f"/api/exams/{ex['exam']}/rubric", headers=login(world, "teacher")).json()
    return out


def draft_v_next(world: dict[str, Any], ex: dict[str, Any]) -> None:
    r = world["client"].put(f"/api/exams/{ex['exam']}/rubric/draft", json={"document": RUBRIC}, headers=login(world, "teacher"))
    assert r.status_code == 200 and r.json()["draft"] is not None, r.text


def approve(world: dict[str, Any], ex: dict[str, Any], who: str, reason: str | None = None) -> Any:
    body = {} if reason is None else {"reason": reason}
    return world["client"].post(f"/api/exams/{ex['exam']}/rubric/approve", json=body, headers=login(world, who))


def workspace(world: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = world["client"].get(f"/api/submissions/{b['sub']}/workspace", headers=login(world, "teacher")).json()
    return out


def test_before_any_grade_a_new_rubric_version_is_approved_freely(world: dict[str, Any], ex: dict[str, Any]) -> None:
    s = state(world, ex)
    assert s["locked"] is False and s["evaluations_given"] == 0
    b = booklet(world, ex, "RG-0")
    assert region(world, b, "q1").status_code == 201  # an answer box is not a grade
    assert state(world, ex)["locked"] is False
    draft_v_next(world, ex)
    r = approve(world, ex, "teacher")
    assert r.status_code == 200 and r.json()["version_no"] == 2


def test_after_the_first_grade_only_an_admin_with_a_reason_can_approve_a_new_version(
    world: dict[str, Any], ex: dict[str, Any]
) -> None:
    graded(world, ex, "RG-1")
    s = state(world, ex)
    assert s["locked"] is True and s["evaluations_given"] == 1
    draft_v_next(world, ex)  # writing a draft is always allowed; approving it is what is guarded
    t = approve(world, ex, "teacher", "I would like a change please")
    assert (
        t.status_code == 409 and t.json()["error"]["code"] == "rubric_locked" and "administrator" in t.json()["error"]["message"]
    )
    assert approve(world, ex, "exA").status_code == 403
    for reason in (None, "", "short", "         "):
        r = approve(world, ex, "admin", reason)
        assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"
    assert state(world, ex)["draft"] is not None  # nothing was approved
    ok = approve(world, ex, "admin", "  Criterion 2 of Q1 was ambiguous; moderation agreed a clearer wording.  ")
    assert ok.status_code == 200 and ok.json()["version_no"] == 2
    with world["sessions"]() as s2:
        row = s2.scalars(select(AuditLog).where(AuditLog.action == "rubric.approve", AuditLog.entity_id == ok.json()["id"])).one()
    assert row.details["after_grading"] is True and row.details["grades_given"] == 1 and row.details["version_no"] == 2
    assert row.details["reason"] == "Criterion 2 of Q1 was ambiguous; moderation agreed a clearer wording."


def test_existing_grades_stay_tied_to_their_version_and_the_workspace_says_so(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "RG-2")
    before = workspace(world, b)
    assert before["rubric_version_no"] == 1 and before["latest_rubric_version_no"] == 1 and before["older_version_grades"] == 0
    assert [e["rubric_version_no"] for e in before["evaluations"]] == [1]
    draft_v_next(world, ex)
    assert approve(world, ex, "admin", "a clearer wording was agreed").status_code == 200
    after = workspace(world, b)
    assert after["rubric_version_no"] == 2 and after["latest_rubric_version_no"] == 2
    assert after["older_version_grades"] == 1  # the grade given under v1 is kept but does not count under v2
    assert after["evaluations"] == [] and after["score"]["total"] == "0"
    with world["sessions"]() as s:
        rows = list(s.scalars(select(Evaluation).where(Evaluation.submission_id == uuid.UUID(b["sub"]))))
    assert len(rows) == 1 and rows[0].marks == 2  # the stored grade is exactly as it was
    # grading again under v2 is a new row under v2; the v1 row is untouched
    assert grade(world, b, "q1", 1, {"c1": "part"}).status_code == 200
    now = workspace(world, b)
    assert [e["rubric_version_no"] for e in now["evaluations"]] == [2] and now["score"]["total"] == "1"
    with world["sessions"]() as s:
        assert sorted(
            e.rubric_version_id == rows[0].rubric_version_id
            for e in s.scalars(select(Evaluation).where(Evaluation.submission_id == uuid.UUID(b["sub"])))
        ) == [False, True]


def test_a_finalized_result_keeps_its_rubric_version_its_snapshot_and_its_numbers(
    world: dict[str, Any], ex: dict[str, Any]
) -> None:
    done = graded(world, ex, "RG-3")
    snap = finalize(world, done).json()
    other = graded(world, ex, "RG-4")  # open: will need re-grading under v2
    draft_v_next(world, ex)
    assert approve(world, ex, "admin", "wording change agreed in moderation").status_code == 200
    ws = workspace(world, done)
    assert ws["finalization"]["state"] == "FINALIZED" and ws["rubric_version_no"] == 1 and ws["latest_rubric_version_no"] == 2
    assert ws["score"]["total"] == "2" and [e["rubric_version_no"] for e in ws["evaluations"]] == [
        1
    ]  # shown under the version it was frozen with
    snaps = world["client"].get(f"/api/submissions/{done['sub']}/snapshots", headers=login(world, "teacher")).json()
    assert [(s["id"], s["total"], s["rubric_version_no"]) for s in snaps] == [(snap["id"], "2", 1)]
    with world["sessions"]() as s:
        assert verify_all(s, submission_id=uuid.UUID(done["sub"])).ok  # recomputes exactly: the snapshot was not touched
        assert (
            s.scalars(select(ResultSnapshot).where(ResultSnapshot.submission_id == uuid.UUID(done["sub"])))
            .one()
            .rubric_version_no
            == 1
        )
    rows = {
        r["student_ref"]: r
        for r in world["client"].get(f"/api/exams/{ex['exam']}/totals", headers=login(world, "teacher")).json()["rows"]
    }
    assert rows["RG-3"]["finalized"] is True and rows["RG-3"]["total"] == "2" and rows["RG-3"]["rubric_version_no"] == 1
    assert (
        rows["RG-4"]["finalized"] is False and rows["RG-4"]["total"] is None
    )  # graded only under v1: nothing counts under v2 yet
    assert rows["RG-3"]["rubric_version_no"] == 1
    an = world["client"].get(f"/api/exams/{ex['exam']}/analytics", headers=login(world, "teacher")).json()
    assert an["booklets"]["other_rubric_version"] == 1 and an["booklets"]["total"] == 1  # RG-3 is counted apart, not mixed in
    sheet = world["client"].get(f"/api/submissions/{done['sub']}/report.pdf", headers=login(world, "teacher"))
    assert sheet.status_code == 200 and snap["id"].encode() in sheet.content  # the result sheet still comes from its snapshot
    assert other["sub"] != done["sub"]


def test_the_first_approval_of_an_exam_is_never_blocked(world: dict[str, Any]) -> None:
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "Fresh", "subject": "S", "total_marks": "5"}, headers=h).json()["id"]
    from test_api_finalize import PAPER

    c.put(f"/api/exams/{eid}/paper/draft", json={"document": PAPER}, headers=h)
    assert c.post(f"/api/exams/{eid}/paper/approve", headers=h).status_code == 200
    assert c.put(f"/api/exams/{eid}/rubric/draft", json={"document": RUBRIC}, headers=h).status_code == 200
    r = c.post(f"/api/exams/{eid}/rubric/approve", headers=h)  # no body at all, as before
    assert r.status_code == 200 and r.json()["version_no"] == 1
