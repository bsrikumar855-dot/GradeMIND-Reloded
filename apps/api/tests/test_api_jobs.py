"""Jobs through the API (spec §15): upload enqueues an ingest job; status; SSE replay + Last-Event-ID; retry of a failed
job; RBAC. The worker is driven in-process (run_job with the real pipelines), so no broker is needed."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from sqlalchemy import update

from grademind_core.db.models import JobStatus, Submission
from grademind_core.jobs import run_job
from grademind_worker.stages import PIPELINES

pytestmark = [needs_db, needs_s3]
PDF = b"%PDF-1.7\n%%EOF\n"


@pytest.fixture(scope="module")
def exam(world: dict[str, Any]) -> str:
    r = world["client"].post(
        "/api/exams", json={"name": "J", "subject": "S", "total_marks": "10"}, headers=login(world, "teacher")
    )
    eid: str = r.json()["id"]
    world["client"].post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    return eid


def upload(w: dict[str, Any], exam_id: str, tag: str) -> dict[str, Any]:
    r = w["client"].post(
        f"/api/exams/{exam_id}/submissions",
        files={"file": ("a.pdf", PDF + tag.encode(), "application/pdf")},
        data={"student_ref": "S-1"},
        headers=login(w, "teacher"),
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def work(w: dict[str, Any], job_id: str) -> JobStatus | None:
    return run_job(w["sessions"], uuid.UUID(job_id), PIPELINES, services={"store": w["store"]})


def sse(
    w: dict[str, Any], job_id: str, who: str = "teacher", last_event_id: str | None = None
) -> list[tuple[str, str | None, Any]]:
    h = login(w, who)
    if last_event_id:
        h["Last-Event-ID"] = last_event_id
    events: list[tuple[str, str | None, Any]] = []
    with w["client"].stream("GET", f"/api/jobs/{job_id}/events", headers=h) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        ev: dict[str, str] = {}
        for line in r.iter_lines():
            if not line:
                if "event" in ev:
                    events.append((ev["event"], ev.get("id"), json.loads(ev["data"])))
                ev = {}
            elif not line.startswith(":"):
                k, _, v = line.partition(":")
                ev[k] = v.strip()
    return events


def test_upload_creates_and_enqueues_an_ingest_job(world: dict[str, Any], exam: str) -> None:
    out = upload(world, exam, "j1")
    assert uuid.UUID(out["job_id"]) in world["queue"].sent
    j = world["client"].get(f"/api/jobs/{out['job_id']}", headers=login(world, "exA")).json()
    assert j["status"] == "QUEUED" and j["kind"] == "ingest" and j["stages"] == []
    assert work(world, out["job_id"]) == JobStatus.COMPLETED
    j = world["client"].get(f"/api/jobs/{out['job_id']}", headers=login(world, "exA")).json()
    assert j["status"] == "COMPLETED" and [(s["stage"], s["status"]) for s in j["stages"]] == [
        ("INTAKE", "STARTED"),
        ("INTAKE", "SUCCEEDED"),
    ]


def test_sse_replays_history_and_resumes_from_last_event_id(world: dict[str, Any], exam: str) -> None:
    jid = upload(world, exam, "j2")["job_id"]
    work(world, jid)
    events = sse(world, jid)
    stage_events = [e for e in events if e[0] == "stage"]
    assert [(e[2]["stage"], e[2]["status"]) for e in stage_events] == [("INTAKE", "STARTED"), ("INTAKE", "SUCCEEDED")]
    assert events[-1][0] == "job" and events[-1][2]["status"] == "COMPLETED"  # terminal: the stream ends
    ids = [int(e[1]) for e in stage_events if e[1]]
    assert ids == sorted(ids)
    resumed = sse(world, jid, last_event_id=str(ids[0]))
    assert [e[2]["status"] for e in resumed if e[0] == "stage"] == ["SUCCEEDED"]  # only what was missed


def test_failed_job_reports_reason_and_retry_reruns_it(world: dict[str, Any], exam: str) -> None:
    out = upload(world, exam, "j3")
    jid = out["job_id"]
    with world["sessions"]() as s, s.begin():  # corrupt the recorded hash: INTAKE must fail
        s.execute(update(Submission).where(Submission.id == uuid.UUID(out["id"])).values(source_sha256="0" * 64))
    assert work(world, jid) == JobStatus.FAILED
    j = world["client"].get(f"/api/jobs/{jid}", headers=login(world, "teacher")).json()
    assert j["status"] == "FAILED" and j["error"].startswith("INTAKE_FAILED: The stored file does not match")
    ev = sse(world, jid)
    assert ev[-1][2]["status"] == "FAILED" and ev[-1][2]["error"] == j["error"]

    assert world["client"].post(f"/api/jobs/{jid}/retry", headers=login(world, "exA")).status_code == 403
    with world["sessions"]() as s, s.begin():  # fix the cause, then retry
        s.execute(update(Submission).where(Submission.id == uuid.UUID(out["id"])).values(source_sha256=out["sha256"]))
    before = len(world["queue"].sent)
    r = world["client"].post(f"/api/jobs/{jid}/retry", headers=login(world, "teacher"))
    assert r.status_code == 202 and r.json()["status"] == "QUEUED" and world["queue"].sent[before:] == [uuid.UUID(jid)]
    assert work(world, jid) == JobStatus.COMPLETED
    r = world["client"].post(f"/api/jobs/{jid}/retry", headers=login(world, "teacher"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_retryable"


def test_job_visibility(world: dict[str, Any], exam: str) -> None:
    jid = upload(world, exam, "j4")["job_id"]
    c = world["client"]
    for who in ("exB", "admin2", "ex2"):
        assert c.get(f"/api/jobs/{jid}", headers=login(world, who)).status_code == 404
        assert c.get(f"/api/jobs/{jid}/events", headers=login(world, who)).status_code == 404
        assert c.post(f"/api/jobs/{jid}/retry", headers=login(world, who)).status_code in (403, 404)
    assert c.get(f"/api/jobs/{jid}").status_code == 401
    assert c.get(f"/api/jobs/{uuid.uuid4()}", headers=login(world, "admin")).status_code == 404


def test_broker_outage_does_not_fail_the_upload(world: dict[str, Any], exam: str) -> None:
    world["queue"].down = True
    try:
        out = upload(world, exam, "j5")
    finally:
        world["queue"].down = False
    j = world["client"].get(f"/api/jobs/{out['job_id']}", headers=login(world, "teacher")).json()
    assert j["status"] == "QUEUED"  # stored and visible; the job was not lost


def test_sse_streams_live_progress_until_terminal(world: dict[str, Any], exam: str) -> None:
    jid = upload(world, exam, "j6")["job_id"]
    t = threading.Timer(0.4, work, args=(world, jid))  # the worker picks the job up while the client is connected
    t.start()
    try:
        events = sse(world, jid)
    finally:
        t.join()
    statuses = [e[2]["status"] for e in events if e[0] == "job"]
    assert statuses[0] == "QUEUED" and statuses[-1] == "COMPLETED"
    assert [e[2]["status"] for e in events if e[0] == "stage"] == ["STARTED", "SUCCEEDED"]
