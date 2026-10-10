"""4.3 analytics through the API: real booklets graded through the real routes; numbers checked by hand; the n < 5 rule; roles;
and independence from OCR. Real Postgres + MinIO."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from fake_ocr import FakeOcr, services
from pdfgen import make_pdf

from grademind_core.db.models import ProcessingJob
from grademind_core.jobs import ensure_ocr_job, run_job
from grademind_worker.stages import PIPELINES

pytestmark = [needs_db, needs_s3]


def lv(i: str, m: str, d: str = "") -> dict[str, str]:
    return {"id": i, "name": i, "marks": m, "definition": d}


PAPER = {
    "total_marks": "4",
    "questions": [{"id": "q1", "label": "1", "max_marks": "2"}, {"id": "q2", "label": "2", "max_marks": "2"}],
}
RUBRIC = {
    "questions": [
        {
            "qid": q,
            "criteria": [{"id": "c1", "name": "Answer", "levels": [lv("none", "0"), lv("part", "1", "half"), lv("full", "2")]}],
        }
        for q in ("q1", "q2")
    ]
}


@pytest.fixture(scope="module")
def ex(world: dict[str, Any]) -> dict[str, Any]:
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "Analytics", "subject": "EVS", "total_marks": "4"}, headers=h).json()["id"]
    for who in ("exA", "exB"):
        c.post(f"/api/exams/{eid}/assignments", json={"user_id": str(world[who])}, headers=login(world, "admin"))
    c.put(f"/api/exams/{eid}/paper/draft", json={"document": PAPER}, headers=h)
    assert c.post(f"/api/exams/{eid}/paper/approve", headers=h).status_code == 200
    assert c.put(f"/api/exams/{eid}/rubric/draft", json={"document": RUBRIC}, headers=h).json()["issues"] == []
    assert c.post(f"/api/exams/{eid}/rubric/approve", headers=h).status_code == 200
    return {"exam": eid, "booklets": []}


def booklet(world: dict[str, Any], ex: dict[str, Any], q1: str | None, q2: str | None = None, who: str = "exA") -> dict[str, Any]:
    """Upload, render, map and grade a booklet. A None level leaves that question unmapped."""
    c, h = world["client"], login(world, "teacher")
    n = len(ex["booklets"]) + 1
    up = c.post(
        f"/api/exams/{ex['exam']}/submissions",
        files={"file": ("b.pdf", make_pdf([[f"AN-{n} " + uuid.uuid4().hex], ["two"]]), "application/pdf")},
        data={"student_ref": f"AN-{n}"},
        headers=h,
    ).json()
    assert str(run_job(world["sessions"], uuid.UUID(up["job_id"]), PIPELINES, services=services(world["store"]))) == "COMPLETED"
    page = c.get(f"/api/submissions/{up['id']}/workspace", headers=h).json()["pages"][0]["id"]
    for qid, level in (("q1", q1), ("q2", q2)):
        if level is None:
            continue
        assert (
            c.post(
                f"/api/submissions/{up['id']}/regions",
                json={"page_id": page, "bbox": [0.1, 0.1, 0.9, 0.4], "qid": qid},
                headers=login(world, who),
            ).status_code
            == 201
        )
        if level != "-":  # "-" = mapped but never graded
            r = c.put(
                f"/api/submissions/{up['id']}/evaluations/{qid}/1", json={"verdicts": {"c1": level}}, headers=login(world, who)
            )
            assert r.status_code == 200, r.text
    b = {"sub": up["id"], "job": up["job_id"]}
    ex["booklets"].append(b)
    return b


def report(world: dict[str, Any], ex: dict[str, Any], scope: str = "all", who: str = "teacher") -> dict[str, Any]:
    r = world["client"].get(f"/api/exams/{ex['exam']}/analytics?scope={scope}", headers=login(world, who))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def row(rep: dict[str, Any], qid: str) -> dict[str, Any]:
    (x,) = [q for q in rep["questions"] if q["qid"] == qid]
    out: dict[str, Any] = x
    return out


def test_roles_and_scope(world: dict[str, Any], ex: dict[str, Any]) -> None:
    c = world["client"]
    url = f"/api/exams/{ex['exam']}/analytics"
    assert c.get(url).status_code == 401
    assert c.get(url, headers=login(world, "exA")).status_code == 403  # graders do not see exam statistics
    assert c.get(url, headers=login(world, "admin2")).status_code == 404
    assert c.get(url + "?scope=nonsense", headers=login(world, "teacher")).status_code == 422
    empty = report(world, ex)
    assert empty["booklets"]["total"] == 0 and row(empty, "q1")["n"] == 0 and row(empty, "q1")["mean"] is None
    no_rubric = c.post(
        "/api/exams", json={"name": "No rubric", "subject": "S", "total_marks": "4"}, headers=login(world, "teacher")
    ).json()["id"]
    assert c.get(f"/api/exams/{no_rubric}/analytics", headers=login(world, "teacher")).status_code == 409


def test_four_booklets_show_counts_and_no_statistics_then_a_fifth_arrives(world: dict[str, Any], ex: dict[str, Any]) -> None:
    for q1, q2 in [("full", "full"), ("full", "part"), ("part", None), ("none", "full")]:
        booklet(world, ex, q1, q2)
    r = report(world, ex)
    q1 = row(r, "q1")
    assert q1["n"] == 4 and q1["suppressed"] and q1["mean"] is None and q1["median"] is None
    assert [(d["marks"], d["count"], d["share"]) for d in q1["distribution"]] == [("0", 1, None), ("1", 1, None), ("2", 2, None)]
    q2 = row(r, "q2")
    assert q2["n"] == 3 and q2["not_attempted"] == 1 and q2["suppressed"]
    assert r["booklets"]["total"] == 4 and r["booklets"]["complete"] == 4

    booklet(world, ex, "full", "none")  # the fifth
    r = report(world, ex)
    q1 = row(r, "q1")
    assert q1["n"] == 5 and not q1["suppressed"]
    assert q1["mean"] == "1.4" and q1["median"] == "2"  # (2 + 2 + 1 + 0 + 2) / 5, hand-computed
    assert [(d["marks"], d["count"], d["share"]) for d in q1["distribution"]] == [
        ("0", 1, "0.2000"),
        ("1", 1, "0.2000"),
        ("2", 3, "0.6000"),
    ]
    crit = q1["criteria"][0]
    assert crit["id"] == "c1" and [(x["id"], x["count"]) for x in crit["levels"]] == [("none", 1), ("part", 1), ("full", 3)]
    assert row(r, "q2")["n"] == 4 and row(r, "q2")["suppressed"]  # 4 graded answers: still no mean


def test_ungraded_overrides_and_time(world: dict[str, Any], ex: dict[str, Any]) -> None:
    before = report(world, ex)
    b = booklet(world, ex, "-", "full")  # Q1 mapped but never graded
    r = report(world, ex)
    assert r["ungraded"]["mapped_but_ungraded_answers"] == before["ungraded"]["mapped_but_ungraded_answers"] + 1
    assert r["booklets"]["in_progress"] == before["booklets"]["in_progress"] + 1
    assert row(r, "q1")["incomplete"] == row(before, "q1")["incomplete"] + 1  # counted apart, not in the marks
    assert row(r, "q1")["n"] == row(before, "q1")["n"]

    saves = r["overrides"]["grades_saved"]
    over = world["client"].put(
        f"/api/submissions/{b['sub']}/evaluations/q2/1",
        json={"verdicts": {"c1": "part"}, "override_reason": "second look"},
        headers=login(world, "exB"),
    )
    assert over.status_code == 200 and over.json()["evaluation"]["is_override"] is True
    r2 = report(world, ex)
    assert r2["overrides"]["grades_saved"] == saves + 1 and r2["overrides"]["overrides"] == r["overrides"]["overrides"] + 1
    assert r2["overrides"]["rate"] is not None  # more than five grades saved by now
    assert r2["time_to_grade"]["n"] >= 5 and r2["time_to_grade"]["median_seconds"] is not None


def test_finalized_scope_uses_only_frozen_results(world: dict[str, Any], ex: dict[str, Any]) -> None:
    c = world["client"]
    assert report(world, ex, "finalized")["booklets"]["total"] == 0
    done = 0
    for b in ex["booklets"]:
        r = c.post(f"/api/submissions/{b['sub']}/finalize", json={"confirm_not_attempted": True}, headers=login(world, "teacher"))
        done += r.status_code == 201
    assert done >= 5
    fin = report(world, ex, "finalized")
    assert fin["booklets"]["total"] == fin["booklets"]["finalized"] == done
    q1 = row(fin, "q1")
    assert q1["n"] >= 5 and not q1["suppressed"]
    # the same numbers as the live population restricted to the same (final) booklets: a frozen result does not drift
    assert row(report(world, ex, "all"), "q1")["mean"] == q1["mean"]


def test_ocr_never_changes_the_numbers(world: dict[str, Any], ex: dict[str, Any]) -> None:
    """Machine-reading a booklet with the (fake) engine leaves every analytics number as it was."""
    before = report(world, ex)
    b = ex["booklets"][0]
    with world["sessions"]() as s:
        ingest = s.get(ProcessingJob, uuid.UUID(b["job"]))
        assert ingest is not None
        sub_id, user_id = ingest.submission_id, ingest.created_by
    assert sub_id is not None
    reading = ensure_ocr_job(world["sessions"], sub_id, user_id)
    assert reading is not None
    fake = FakeOcr()
    assert run_job(world["sessions"], reading, PIPELINES, services=services(world["store"], fake)).value == "COMPLETED"
    assert fake.calls == 2  # the engine really did read both pages
    assert report(world, ex) == before
