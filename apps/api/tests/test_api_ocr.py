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
