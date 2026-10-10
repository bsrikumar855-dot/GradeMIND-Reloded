"""Machine-reading API (3.1, D28): grading readiness does not depend on OCR; summary; re-read; RBAC; audit."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from fake_ocr import FakeOcr, services
from pdfgen import make_pdf
from sqlalchemy import select

from grademind_core.db.models import AuditLog
from grademind_core.jobs import run_job
from grademind_worker.stages import PIPELINES

pytestmark = [needs_db, needs_s3]


@pytest.fixture(scope="module")
def exam(world: dict[str, Any]) -> str:
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "OCR", "subject": "S", "total_marks": "5"}, headers=h).json()["id"]
    c.post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    return eid


def upload(w: dict[str, Any], exam_id: str, tag: str) -> dict[str, Any]:
    r = w["client"].post(
        f"/api/exams/{exam_id}/submissions",
        files={"file": ("b.pdf", make_pdf([[f"{tag} {uuid.uuid4().hex}"], ["page two"]]), "application/pdf")},
        data={"student_ref": tag},
        headers=login(w, "teacher"),
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def work(w: dict[str, Any], job_id: str, fake: FakeOcr | None = None, **kw: Any) -> Any:
    return run_job(w["sessions"], uuid.UUID(job_id), PIPELINES, services=services(w["store"], fake or FakeOcr(), **kw))


def row(w: dict[str, Any], exam_id: str, sid: str, who: str = "teacher") -> dict[str, Any]:
    rows = w["client"].get(f"/api/exams/{exam_id}/submissions", headers=login(w, who)).json()
    (r,) = [x for x in rows if x["id"] == sid]
    out: dict[str, Any] = r
    return out


def test_grading_is_ready_when_pages_are_rendered_even_if_ocr_fails(world: dict[str, Any], exam: str) -> None:
    """The hard D28 rule: a dead OCR service makes the job FAILED, but the booklet can be graded."""
    up = upload(world, exam, "RDY-1")
    assert not row(world, exam, up["id"])["pages_ready"]  # nothing has run yet
    assert work(world, up["job_id"], FakeOcr(health_down=True)).value == "FAILED"
    r = row(world, exam, up["id"])
    assert r["job_status"] == "FAILED" and r["job_stage"] == "OCR" and r["page_count"] == 2 and r["pages_ready"] is True
    assert r["job_error"].startswith("OCR_FAILED:") and "Grading is not affected" in r["job_error"]


def test_summary_counts_read_and_failed_pages(world: dict[str, Any], exam: str) -> None:
    up = upload(world, exam, "SUM-1")
    assert work(world, up["job_id"], FakeOcr(fail_calls={2})).value == "COMPLETED"
    rows = world["client"].get(f"/api/exams/{exam}/ocr-summary", headers=login(world, "exA")).json()
    (mine,) = [r for r in rows if r["submission_id"] == up["id"]]
    assert (mine["pages"], mine["pages_read"], mine["pages_failed"]) == (2, 1, 1)
    for who in ("exB", "admin2"):
        assert world["client"].get(f"/api/exams/{exam}/ocr-summary", headers=login(world, who)).status_code == 404


def test_reread_rereads_only_the_failed_pages(world: dict[str, Any], exam: str) -> None:
    c = world["client"]
    up = upload(world, exam, "RR-1")
    work(world, up["job_id"], FakeOcr(fail_calls={1}))
    r = c.post(f"/api/submissions/{up['id']}/ocr/retry", headers=login(world, "exA"))  # an examiner may ask for it
    assert r.status_code == 202, r.text
    job = r.json()["job_id"]
    assert uuid.UUID(job) in world["queue"].sent
    assert (
        c.post(f"/api/submissions/{up['id']}/ocr/retry", headers=login(world, "exA")).json()["error"]["code"] == "already_running"
    )
    fake = FakeOcr()
    assert work(world, job, fake).value == "COMPLETED" and fake.calls == 1  # the one failed page
    rows = c.get(f"/api/exams/{exam}/ocr-summary", headers=login(world, "exA")).json()
    (mine,) = [x for x in rows if x["submission_id"] == up["id"]]
    assert (mine["pages_read"], mine["pages_failed"]) == (2, 0)
    with world["sessions"]() as s:
        assert "ocr.retry_requested" in set(s.scalars(select(AuditLog.action).where(AuditLog.entity_id == up["id"])))


def test_reread_is_scoped_and_authenticated(world: dict[str, Any], exam: str) -> None:
    up = upload(world, exam, "RR-2")
    work(world, up["job_id"])
    c = world["client"]
    for who in ("exB", "admin2", "ex2"):
        assert c.post(f"/api/submissions/{up['id']}/ocr/retry", headers=login(world, who)).status_code == 404
    assert c.post(f"/api/submissions/{up['id']}/ocr/retry").status_code == 401
    assert c.post(f"/api/submissions/{uuid.uuid4()}/ocr/retry", headers=login(world, "admin")).status_code == 404


# --------------------------------------------------------------------------------------- 3.2: machine reading per region


def lv(i: str, m: str, d: str = "") -> dict[str, str]:
    return {"id": i, "name": i, "marks": m, "definition": d}


@pytest.fixture(scope="module")
def graded_exam(world: dict[str, Any]) -> str:
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "MR", "subject": "S", "total_marks": "5"}, headers=h).json()["id"]
    c.post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    paper = {
        "total_marks": "5",
        "questions": [{"id": "q1", "label": "1", "max_marks": "2"}, {"id": "q2", "label": "2", "max_marks": "3"}],
    }
    assert c.put(f"/api/exams/{eid}/paper/draft", json={"document": paper}, headers=h).status_code == 200
    assert c.post(f"/api/exams/{eid}/paper/approve", headers=h).status_code == 200
    rubric = {
        "questions": [
            {"qid": "q1", "criteria": [{"id": "c1", "name": "D", "levels": [lv("none", "0"), lv("full", "2")]}]},
            {"qid": "q2", "criteria": [{"id": "c1", "name": "C", "levels": [lv("none", "0"), lv("full", "3")]}]},
        ]
    }
    assert c.put(f"/api/exams/{eid}/rubric/draft", json={"document": rubric}, headers=h).status_code == 200
    assert c.post(f"/api/exams/{eid}/rubric/approve", headers=h).status_code == 200
    return eid


def pages_of(w: dict[str, Any], sid: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = w["client"].get(f"/api/submissions/{sid}/pages", headers=login(w, "exA")).json()
    return out


def draw(w: dict[str, Any], sid: str, page_id: str, qid: str, bbox: list[float], who: str = "exA") -> dict[str, Any]:
    r = w["client"].post(
        f"/api/submissions/{sid}/regions", json={"page_id": page_id, "bbox": bbox, "qid": qid}, headers=login(w, who)
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def reading(w: dict[str, Any], sid: str, who: str = "exA") -> dict[str, Any]:
    r = w["client"].get(f"/api/submissions/{sid}/machine-reading", headers=login(w, who))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def test_lines_inside_a_region_come_back_in_reading_order_with_a_may_be_wrong_notice(
    world: dict[str, Any], graded_exam: str
) -> None:
    up = upload(world, graded_exam, "MR-1")
    work(world, up["job_id"])
    p1, p2 = pages_of(world, up["id"])
    draw(world, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.15])  # covers the first fake line only
    draw(world, up["id"], p1["id"], "q2", [0.05, 0.05, 0.95, 0.30])  # covers both lines
    draw(world, up["id"], p2["id"], "q2", [0.05, 0.05, 0.95, 0.30], who="admin")
    mr = reading(world, up["id"])
    assert "can be wrong" in mr["notice"] and mr["low_confidence_below"] == 0.8
    only_first, both, other_page = mr["regions"]
    assert [ln["text"] for ln in only_first["lines"]] == ["page 1 first line"] and only_first["page_status"] == "read"
    assert [ln["text"] for ln in both["lines"]] == ["page 1 first line", "page 1 second line"]
    first, second = both["lines"]
    assert (first["score"], first["low_confidence"], first["overlap"]) == (0.93, False, 1.0)
    assert (second["score"], second["low_confidence"]) == (0.41, True)  # flagged for a closer look
    # the same fractional space as the answer regions (the stored box is in whole pixels, hence the 1-pixel tolerance)
    assert first["bbox"] == pytest.approx([0.1, 0.1, 0.9, 0.13], abs=1 / 1240)
    assert [ln["text"] for ln in other_page["lines"]] == ["page 2 first line", "page 2 second line"] and other_page[
        "page_no"
    ] == 2


def test_pages_without_machine_text_are_reported_not_hidden(world: dict[str, Any], graded_exam: str) -> None:
    c = world["client"]
    up = upload(world, graded_exam, "MR-2")
    # rendered but never machine-read (the OCR stage has not run: the service is down)
    assert work(world, up["job_id"], FakeOcr(health_down=True)).value == "FAILED"
    p1, p2 = pages_of(world, up["id"])
    draw(world, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.30])
    draw(world, up["id"], p2["id"], "q2", [0.05, 0.05, 0.95, 0.30])
    first, second = reading(world, up["id"])["regions"]
    assert (first["page_status"], first["lines"]) == ("unread", []) and (second["page_status"], second["lines"]) == ("unread", [])
    # page 1 fails to read, page 2 reads
    job = c.post(f"/api/submissions/{up['id']}/ocr/retry", headers=login(world, "exA")).json()["job_id"]
    assert work(world, job, FakeOcr(fail_calls={1})).value == "COMPLETED"
    first, second = reading(world, up["id"])["regions"]
    assert (first["page_status"], first["lines"]) == ("failed", [])
    assert second["page_status"] == "read" and len(second["lines"]) == 2  # the other page is unaffected


def test_deleted_regions_drop_out_and_crossed_out_ones_say_so(world: dict[str, Any], graded_exam: str) -> None:
    c = world["client"]
    up = upload(world, graded_exam, "MR-3")
    work(world, up["job_id"])
    p1, _ = pages_of(world, up["id"])
    keep = draw(world, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.30])
    gone = draw(world, up["id"], p1["id"], "q2", [0.05, 0.05, 0.95, 0.30])
    assert c.delete(f"/api/submissions/{up['id']}/regions/{gone['id']}", headers=login(world, "exA")).status_code == 204
    c.post(f"/api/submissions/{up['id']}/regions/{keep['id']}/crossed", json={"crossed_out": True}, headers=login(world, "exA"))
    (only,) = reading(world, up["id"])["regions"]
    assert only["region_id"] == keep["id"] and only["crossed_out"] is True


def test_machine_reading_is_scoped_like_the_booklet(world: dict[str, Any], graded_exam: str) -> None:
    up = upload(world, graded_exam, "MR-4")
    work(world, up["job_id"])
    c = world["client"]
    for who in ("exB", "admin2", "ex2"):
        assert c.get(f"/api/submissions/{up['id']}/machine-reading", headers=login(world, who)).status_code == 404
    assert c.get(f"/api/submissions/{up['id']}/machine-reading").status_code == 401
    assert c.get(f"/api/submissions/{uuid.uuid4()}/machine-reading", headers=login(world, "admin")).status_code == 404


def test_grading_is_identical_with_and_without_machine_reading(world: dict[str, Any], graded_exam: str) -> None:
    """D28 from the data side: the same booklet, regions and verdicts score the same whether or not it was ever machine-read,
    and no machine text appears anywhere in the grading workspace."""
    c = world["client"]

    def graded(tag: str, ocr: bool) -> tuple[dict[str, Any], str]:
        up = upload(world, graded_exam, tag)
        work(world, up["job_id"], FakeOcr() if ocr else FakeOcr(health_down=True))
        p1, p2 = pages_of(world, up["id"])
        draw(world, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.30])
        draw(world, up["id"], p2["id"], "q2", [0.05, 0.05, 0.95, 0.30])
        for qid, level in (("q1", "full"), ("q2", "none")):
            r = c.put(
                f"/api/submissions/{up['id']}/evaluations/{qid}/1", json={"verdicts": {"c1": level}}, headers=login(world, "exA")
            )
            assert r.status_code == 200, r.text
        ws = c.get(f"/api/submissions/{up['id']}/workspace", headers=login(world, "exA"))
        return ws.json(), ws.text

    with_ocr, with_text = graded("EQ-1", ocr=True)
    without_ocr, without_text = graded("EQ-2", ocr=False)
    assert with_ocr["score"] == without_ocr["score"] and with_ocr["score"]["total"] == "2"
    assert [e["marks"] for e in with_ocr["evaluations"]] == [e["marks"] for e in without_ocr["evaluations"]]
    assert "first line" not in with_text and "second line" not in with_text  # the workspace carries no machine text at all
