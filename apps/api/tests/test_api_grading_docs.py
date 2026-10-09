"""Question paper + rubric through the API (D25 a/b): parse, drafts with issues, approval gates, immutable approved
versions (database trigger), versioning after approval, stale rubric drafts, RBAC, audit."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from pdfgen import make_pdf
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from grademind_core.db.models import AuditLog, PaperVersion, RubricVersion

pytestmark = needs_db

PAPER_TEXT = "1. Define ecosystem. [2]\n2. (a) Explain the water cycle. [3]\n(b) What is smog? [1]"


@pytest.fixture(scope="module")
def exam(world: dict[str, Any]) -> str:
    r = world["client"].post(
        "/api/exams", json={"name": "Docs", "subject": "EVS", "total_marks": "6"}, headers=login(world, "teacher")
    )
    eid: str = r.json()["id"]
    world["client"].post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    return eid


def lv(i: str, m: str, d: str = "") -> dict[str, str]:
    return {"id": i, "name": i, "marks": m, "definition": d}


def rubric_doc() -> dict[str, Any]:
    return {
        "questions": [
            {
                "qid": "q1",
                "criteria": [
                    {
                        "id": "c1",
                        "name": "Definition",
                        "levels": [lv("none", "0"), lv("part", "1", "half right"), lv("full", "2")],
                    }
                ],
            },
            {"qid": "q2a", "criteria": [{"id": "c1", "name": "Cycle", "levels": [lv("none", "0"), lv("full", "3")]}]},
            {"qid": "q2b", "criteria": [{"id": "c1", "name": "Smog", "levels": [lv("none", "0"), lv("full", "1")]}]},
        ]
    }


def test_parse_then_save_draft_then_approve(world: dict[str, Any], exam: str) -> None:
    c, h = world["client"], login(world, "teacher")
    parsed = c.post("/api/paper/parse", json={"text": PAPER_TEXT}, headers=h).json()
    assert parsed["warnings"] == [] and parsed["draft"]["total_marks"] == "6"

    bad = dict(parsed["draft"], total_marks="10")
    r = c.put(f"/api/exams/{exam}/paper/draft", json={"document": bad}, headers=h)
    assert r.status_code == 200 and {i["code"] for i in r.json()["issues"]} == {"total_mismatch", "exam_total_mismatch"}
    r = c.post(f"/api/exams/{exam}/paper/approve", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "draft_has_issues" and len(r.json()["error"]["issues"]) == 2

    r = c.put(f"/api/exams/{exam}/paper/draft", json={"document": parsed["draft"]}, headers=h)
    assert r.json()["issues"] == [] and r.json()["draft"]["version_no"] == 1  # same draft row, updated
    r = c.post(f"/api/exams/{exam}/paper/approve", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    state = c.get(f"/api/exams/{exam}/paper", headers=login(world, "exA")).json()  # examiners may read
    assert state["draft"] is None and state["approved"]["version_no"] == 1


def test_schema_errors_are_listed_with_paths(world: dict[str, Any], exam: str) -> None:
    doc = {"total_marks": "6", "questions": [{"id": "Q 1", "label": "1", "max_marks": "x"}]}
    r = world["client"].put(f"/api/exams/{exam}/paper/draft", json={"document": doc}, headers=login(world, "teacher"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_document"
    paths = {i["path"] for i in r.json()["error"]["issues"]}
    assert "paper/questions/0/id" in paths and "paper/questions/0/max_marks" in paths


def test_approved_versions_are_immutable_in_the_database(world: dict[str, Any], exam: str) -> None:
    with world["sessions"]() as s:
        v = s.scalars(
            select(PaperVersion).where(PaperVersion.exam_id == uuid.UUID(exam), PaperVersion.status == "APPROVED")
        ).first()
        assert v is not None
        vid = v.id
    for stmt in (
        update(PaperVersion).where(PaperVersion.id == vid).values(document={"tampered": True}),
        text("DELETE FROM paper_versions WHERE id = :id").bindparams(id=vid),
    ):
        with pytest.raises(DBAPIError, match="immutable"), world["sessions"]() as s, s.begin():
            s.execute(stmt)


def test_rubric_lifecycle(world: dict[str, Any], exam: str) -> None:
    c, h = world["client"], login(world, "teacher")
    r = c.put(f"/api/exams/{exam}/rubric/draft", json={"document": {"questions": rubric_doc()["questions"][:2]}}, headers=h)
    assert r.status_code == 200 and [i["code"] for i in r.json()["issues"]] == ["missing_rubric"]
    r = c.put(
        f"/api/exams/{exam}/rubric/draft", json={"document": rubric_doc(), "policy": {"rounding_mode": "sideways"}}, headers=h
    )
    assert r.status_code == 422 and r.json()["error"]["issues"][0]["path"].startswith("policy/")
    r = c.put(
        f"/api/exams/{exam}/rubric/draft", json={"document": rubric_doc(), "policy": {"multiple_attempts": "best"}}, headers=h
    )
    assert r.json()["issues"] == [] and not r.json()["stale_draft"]
    assert (
        c.put(f"/api/exams/{exam}/rubric/draft", json={"document": rubric_doc()}, headers=login(world, "exA")).status_code == 403
    )
    r = c.post(f"/api/exams/{exam}/rubric/approve", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["policy"]["multiple_attempts"] == "best"
    with pytest.raises(DBAPIError, match="immutable"), world["sessions"]() as s, s.begin():
        s.execute(update(RubricVersion).where(RubricVersion.id == uuid.UUID(r.json()["id"])).values(policy={}))


def test_changes_after_approval_create_new_versions_and_stale_rubrics_are_caught(world: dict[str, Any], exam: str) -> None:
    c, h = world["client"], login(world, "teacher")
    v1 = c.get(f"/api/exams/{exam}/paper", headers=h).json()["approved"]
    r = c.put(f"/api/exams/{exam}/rubric/draft", json={"document": rubric_doc()}, headers=h)  # rubric v2 draft on paper v1
    assert r.json()["draft"]["version_no"] == 2
    doc = dict(v1["document"])
    doc["questions"] = [dict(doc["questions"][0], text="Define an ecosystem (edited).")] + doc["questions"][1:]
    assert c.put(f"/api/exams/{exam}/paper/draft", json={"document": doc}, headers=h).json()["draft"]["version_no"] == 2
    assert c.post(f"/api/exams/{exam}/paper/approve", headers=h).status_code == 200
    state = c.get(f"/api/exams/{exam}/paper", headers=h).json()
    assert state["approved"]["version_no"] == 2
    with world["sessions"]() as s:  # v1 untouched
        old = s.get(PaperVersion, uuid.UUID(v1["id"]))
        assert old is not None and old.document == v1["document"] and old.status == "APPROVED"
    rs = c.get(f"/api/exams/{exam}/rubric", headers=h).json()
    assert rs["stale_draft"] is True
    r = c.post(f"/api/exams/{exam}/rubric/approve", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "paper_changed"
    c.put(f"/api/exams/{exam}/rubric/draft", json={"document": rubric_doc()}, headers=h)
    assert c.post(f"/api/exams/{exam}/rubric/approve", headers=h).status_code == 200


def test_rubric_needs_an_approved_paper_and_scope_rules(world: dict[str, Any]) -> None:
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "Empty", "subject": "S", "total_marks": "5"}, headers=h).json()["id"]
    r = c.put(f"/api/exams/{eid}/rubric/draft", json={"document": rubric_doc()}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "paper_not_approved"
    assert c.post(f"/api/exams/{eid}/paper/approve", headers=h).json()["error"]["code"] == "no_draft"
    assert c.post(f"/api/exams/{eid}/rubric/approve", headers=h).json()["error"]["code"] == "no_draft"
    for who in ("admin2", "exB"):
        assert c.get(f"/api/exams/{eid}/paper", headers=login(world, who)).status_code == 404
    assert c.post("/api/paper/parse", json={"text": "1. x [1]"}, headers=login(world, "exA")).status_code == 403


def test_actions_are_audited(world: dict[str, Any], exam: str) -> None:
    with world["sessions"]() as s:
        actions = set(
            s.scalars(select(AuditLog.action).where(AuditLog.action.like("paper.%") | AuditLog.action.like("rubric.%")))
        )
    assert {"paper.draft_save", "paper.approve", "rubric.draft_save", "rubric.approve"} <= actions


@needs_s3
def test_paper_source_upload_extracts_the_text_layer(world: dict[str, Any], exam: str) -> None:
    c, h = world["client"], login(world, "teacher")
    pdf = make_pdf([["1. Define ecosystem. [2]", "2. (a) Explain the water cycle. [3]"], ["(b) What is smog? [1]"]])
    r = c.post(f"/api/exams/{exam}/paper/source", files={"file": ("paper.pdf", pdf, "application/pdf")}, headers=h)
    assert r.status_code == 201 and r.json()["has_text_layer"]
    parsed = c.post("/api/paper/parse", json={"text": r.json()["text"]}, headers=h).json()
    assert parsed["draft"]["total_marks"] == "6" and parsed["warnings"] == []
