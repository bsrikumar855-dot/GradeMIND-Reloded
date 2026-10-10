"""OCR stage (3.1, D28) against real Postgres + MinIO, with a fake OCR client behind the REAL provider registry.

What must hold: page failures never fail the job; systemic failures fail only the OCR stage and a retry re-runs only what is
missing; outputs are idempotent (one successful run per page and engine config); the resolved engine config is stored and
logged (rule 12); a disabled provider is never called; the lease is kept while the stage is slow; the tables are append-only.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import timedelta

import pytest
from drive import drive
from fake_ocr import RESOLVED, FakeOcr, services
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from worker_world import needs_stack, new_booklet_job

from grademind_core.db.models import JobStageAttempt, JobStatus, Page, ProcessingJob, StageStatus
from grademind_core.db.ocr_models import OcrLine, OcrRun
from grademind_core.jobs import LeasePolicy, Pipeline, RetryPolicy, ensure_ocr_job, request_retry, run_job
from grademind_core.ocr_client import OcrPageResult
from grademind_core.ocr_runs import config_hash, record_ok
from grademind_core.storage import ObjectStore
from grademind_worker.stages import INGEST, OCR_REREAD, OCR_RETRY, PIPELINES

pytest_plugins = ["worker_world"]
pytestmark = needs_stack
Env = tuple[sessionmaker[Session], ObjectStore]
NO_AUTO_RETRY = RetryPolicy(
    max_retries=0
)  # these tests look at what one failure leaves behind; test_ocr_retry.py covers the retries


def ingest_and_read(
    db: sessionmaker[Session], store: ObjectStore, jid: uuid.UUID, fake: FakeOcr, **kw: object
) -> tuple[JobStatus | None, uuid.UUID]:
    """The ingest job, then the machine-reading job the worker creates after it. Returns (status of the reading, its job id)."""
    status, ran = drive(db, jid, PIPELINES, services(store, fake, **kw), NO_AUTO_RETRY)
    assert len(ran) == 2, "ingest completed, so a reading job follows"
    return status, ran[1]


def submission_of(db: sessionmaker[Session], jid: uuid.UUID) -> uuid.UUID:
    with db() as s:
        job = s.get(ProcessingJob, jid)
        assert job is not None and job.submission_id is not None
        return job.submission_id


def runs_by_page(db: sessionmaker[Session], jid: uuid.UUID) -> dict[int, list[OcrRun]]:
    sub = submission_of(db, jid)
    out: dict[int, list[OcrRun]] = {}
    with db() as s:
        for run, no in s.execute(
            select(OcrRun, Page.page_no)
            .join(Page, Page.id == OcrRun.page_id)
            .where(Page.submission_id == sub)
            .order_by(Page.page_no, OcrRun.created_at)
        ):
            out.setdefault(no, []).append(run)
    return out


def stage_log(db: sessionmaker[Session], jid: uuid.UUID) -> list[tuple[str, str]]:
    with db() as s:
        rows = s.scalars(select(JobStageAttempt).where(JobStageAttempt.job_id == jid).order_by(JobStageAttempt.seq))
        return [(a.stage, a.status.value) for a in rows]


def last_output(db: sessionmaker[Session], jid: uuid.UUID, stage: str) -> str | None:
    with db() as s:
        row = s.scalars(
            select(JobStageAttempt)
            .where(JobStageAttempt.job_id == jid, JobStageAttempt.stage == stage, JobStageAttempt.status == StageStatus.SUCCEEDED)
            .order_by(JobStageAttempt.seq.desc())
        ).first()
        return row.output_ref if row else None


def new_ocr_job(db: sessionmaker[Session], sub: uuid.UUID, kind: str = OCR_RETRY) -> uuid.UUID:
    with db() as s, s.begin():
        old = s.scalars(select(ProcessingJob).where(ProcessingJob.submission_id == sub)).first()
        assert old is not None
        job = ProcessingJob(kind=kind, submission_id=sub, created_by=old.created_by)
        s.add(job)
        s.flush()
        return job.id


def test_pages_are_read_and_the_resolved_config_is_stored_and_logged(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    db, store = env
    fake = FakeOcr()
    jid = new_booklet_job(db, store, pages=2)
    with caplog.at_level(logging.INFO, logger="grademind.worker"):
        status, rid = ingest_and_read(db, store, jid, fake)
    assert status == JobStatus.COMPLETED
    assert last_output(db, rid, "OCR") == "ocr:ok=2,failed=0,skipped=0"
    runs = runs_by_page(db, jid)
    assert sorted(runs) == [1, 2] and all(len(r) == 1 and r[0].status == "OK" for r in runs.values())
    run = runs[1][0]
    assert run.config_hash == config_hash(RESOLVED) and run.resolved == RESOLVED and run.provider == "paddle_v6"
    assert run.model_names == {"det": "PP-OCRv6_medium_det", "rec": "PP-OCRv6_medium_rec"}
    assert run.weights_sha256["rec"] == {"inference.pdiparams": "r" * 64} and (run.image_width, run.image_height) == (1240, 1755)
    with db() as s:
        lines = s.scalars(select(OcrLine).where(OcrLine.ocr_run_id == run.id).order_by(OcrLine.line_no)).all()
    assert [(ln.text, ln.score) for ln in lines] == [("page 1 first line", 0.93), ("page 1 second line", 0.41)]
    # rule 12: one structured line per stage run with the engine that was ACTUALLY loaded
    events = [
        json.loads(r.getMessage()) for r in caplog.records if r.name == "grademind.worker" and '"ocr.run"' in r.getMessage()
    ]
    assert len(events) == 1 and events[0]["config_hash"] == run.config_hash and events[0]["pages"] == 2
    assert events[0]["models"]["det"] == {"name": "PP-OCRv6_medium_det", "sha256": {"inference.pdiparams": "d" * 64}}
    assert events[0]["libraries"] == RESOLVED["libraries"]


def test_a_failed_page_never_fails_the_job(env: Env) -> None:
    db, store = env
    fake = FakeOcr(fail_calls={2})
    jid = new_booklet_job(db, store, pages=3)
    status, rid = ingest_and_read(db, store, jid, fake)
    assert status == JobStatus.COMPLETED
    assert last_output(db, rid, "OCR") == "ocr:ok=2,failed=1,skipped=0"
    runs = runs_by_page(db, jid)
    assert [runs[n][0].status for n in (1, 2, 3)] == ["OK", "FAILED", "OK"]
    assert runs[2][0].error == "undecodable: The image could not be decoded."  # safe reason; never student text
    with db() as s:  # the failed page has no lines at all
        assert s.scalar(select(OcrLine.id).where(OcrLine.ocr_run_id == runs[2][0].id)) is None


def test_service_down_at_the_start_fails_only_ocr_and_a_retry_reruns_only_ocr(env: Env) -> None:
    db, store = env
    fake = FakeOcr(health_down=True)
    jid = new_booklet_job(db, store, pages=2)
    status, rid = ingest_and_read(db, store, jid, fake)
    assert status == JobStatus.FAILED
    with db() as s:
        ingest = s.get(ProcessingJob, jid)
        assert ingest is not None and ingest.status == JobStatus.COMPLETED  # rendering is done: grading is possible
        job = s.get(ProcessingJob, rid)
        assert job is not None and job.error is not None
        assert job.error.startswith("OCR_FAILED: The text-reading service is not available right now. Grading is not affected")
    sub = submission_of(db, jid)
    with db() as s:
        assert len(s.scalars(select(Page).where(Page.submission_id == sub)).all()) == 2  # the pages exist
    assert fake.calls == 0

    fake.health_down = False
    with db() as s:
        job = s.get(ProcessingJob, rid)
        assert job is not None and request_retry(s, job)
        s.commit()
    assert run_job(db, rid, PIPELINES, services=services(store, fake)) == JobStatus.COMPLETED
    assert [st for st, status in stage_log(db, jid) if status == "STARTED"] == ["INTAKE", "RASTERIZE"]  # ingest never re-ran
    assert [st for st, status in stage_log(db, rid) if status == "STARTED"] == ["OCR", "OCR"]
    assert all(r[0].status == "OK" for r in runs_by_page(db, jid).values())


def test_service_dying_mid_run_keeps_finished_pages_and_the_retry_reads_only_the_rest(env: Env) -> None:
    db, store = env
    fake = FakeOcr(down_from=2)  # page 1 reads, then the service dies
    jid = new_booklet_job(db, store, pages=3)
    status, rid = ingest_and_read(db, store, jid, fake, abort_after=2)
    assert status == JobStatus.FAILED
    with db() as s:
        job = s.get(ProcessingJob, rid)
        assert job is not None and (job.error or "").startswith("OCR_FAILED: 2 page(s) were not read")
    assert fake.calls == 3  # page 1 ok, page 2 down, page 3 down -> gave up
    runs = runs_by_page(db, jid)
    assert list(runs) == [1] and runs[1][0].status == "OK"  # unavailability is not recorded as a page failure

    fake.down_from, fake.calls = None, 0
    with db() as s:
        job = s.get(ProcessingJob, rid)
        assert job is not None and request_retry(s, job)
        s.commit()
    assert run_job(db, rid, PIPELINES, services=services(store, fake)) == JobStatus.COMPLETED
    assert fake.calls == 2  # pages 2 and 3 only: page 1 was skipped
    assert last_output(db, rid, "OCR") == "ocr:ok=2,failed=0,skipped=1"
    assert [len(r) for r in runs_by_page(db, jid).values()] == [1, 1, 1]


def test_ocr_retry_pipeline_rereads_only_failed_pages(env: Env) -> None:
    db, store = env
    fake = FakeOcr(fail_calls={1})
    jid = new_booklet_job(db, store, pages=2)
    status, _first = ingest_and_read(db, store, jid, fake)
    assert status == JobStatus.COMPLETED
    assert [r[0].status for r in runs_by_page(db, jid).values()] == ["FAILED", "OK"]
    rid = new_ocr_job(db, submission_of(db, jid))
    fake.calls, fake.fail_calls = 0, set()
    assert run_job(db, rid, PIPELINES, services=services(store, fake)) == JobStatus.COMPLETED
    assert fake.calls == 1 and last_output(db, rid, "OCR") == "ocr:ok=1,failed=0,skipped=1"
    runs = runs_by_page(db, jid)
    assert [r.status for r in runs[1]] == ["FAILED", "OK"] and [r.status for r in runs[2]] == [
        "OK"
    ]  # history kept, never rewritten


def test_duplicate_and_concurrent_runs_cannot_double_write(env: Env) -> None:
    db, store = env
    fake = FakeOcr(delay_s=0.3)
    first = new_booklet_job(db, store, pages=2)
    assert ingest_and_read(db, store, first, FakeOcr())[0] == JobStatus.COMPLETED
    sub = submission_of(db, first)
    # two re-read jobs for a submission whose pages have NO successful run for this config (a different engine) race each other
    other = FakeOcr(delay_s=0.3, resolved={**RESOLVED, "libraries": {"paddlepaddle": "9.9.9", "paddleocr": "9.9.9"}})
    # two readings of one booklet can never be active together (test_ocr_retry.py); this test is about the STAGE's own
    # idempotency, so its two jobs have a test-only kind that the unique index does not cover
    pipelines = {**PIPELINES, "ocr_race": Pipeline(kind="ocr_race", stages=(OCR_REREAD,))}
    j1, j2 = new_ocr_job(db, sub, "ocr_race"), new_ocr_job(db, sub, "ocr_race")
    results: list[JobStatus | None] = []
    threads = [
        threading.Thread(target=lambda j=j: results.append(run_job(db, j, pipelines, services=services(store, other))))
        for j in (j1, j2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [JobStatus.COMPLETED, JobStatus.COMPLETED]
    new_hash = config_hash(other.resolved)
    with db() as s:
        per_page = s.execute(
            select(OcrRun.page_id, text("count(*)"))
            .where(OcrRun.config_hash == new_hash, OcrRun.status == "OK")
            .group_by(OcrRun.page_id)
        ).all()
        n_lines = s.execute(
            text("select count(*) from ocr_lines l join ocr_runs r on r.id = l.ocr_run_id where r.config_hash = :h"),
            {"h": new_hash},
        ).scalar_one()
    assert len(per_page) == 2 and all(c == 1 for _, c in per_page)  # exactly one successful run per page for that engine
    assert n_lines == 4  # 2 pages x 2 lines: the loser of each race wrote nothing
    assert fake.calls == 0


def test_record_ok_twice_for_one_page_and_config_writes_once(env: Env) -> None:
    db, store = env
    jid = new_booklet_job(db, store, pages=1)
    assert (
        run_job(db, jid, {INGEST: PIPELINES[INGEST]}, services=services(store, FakeOcr(), enabled=False)) == JobStatus.COMPLETED
    )
    with db() as s:
        page = s.scalars(select(Page).where(Page.submission_id == submission_of(db, jid))).one()
    res = OcrPageResult(width=page.width, height=page.height, lines=[], latency_s=0.0, engine={})
    cfg = config_hash(RESOLVED)
    with db() as s:
        assert record_ok(s, page.id, "paddle_v6", RESOLVED, cfg, "t", res) is True
    with db() as s:
        assert record_ok(s, page.id, "paddle_v6", RESOLVED, cfg, "t", res) is False
    with db() as s:
        assert len(s.scalars(select(OcrRun).where(OcrRun.page_id == page.id)).all()) == 1


def test_a_disabled_provider_is_never_called(env: Env) -> None:
    db, store = env
    fake = FakeOcr()
    jid = new_booklet_job(db, store, pages=2)
    status, rid = ingest_and_read(db, store, jid, fake, enabled=False)
    assert status == JobStatus.COMPLETED
    assert last_output(db, rid, "OCR") == "ocr:disabled" and fake.calls == 0 and runs_by_page(db, jid) == {}


def test_engine_swapped_mid_run_or_wrong_image_size_is_recorded_not_stored_as_text(env: Env) -> None:
    db, store = env
    swapped = FakeOcr(
        engine={"libraries": RESOLVED["libraries"], "models": {"det": {"name": "PP-OCRv5_server_det", "sha256": {}}}}
    )
    jid = new_booklet_job(db, store, pages=1)
    assert ingest_and_read(db, store, jid, swapped)[0] == JobStatus.COMPLETED
    (run,) = runs_by_page(db, jid)[1]
    assert run.status == "FAILED" and (run.error or "").startswith("engine_changed:")

    wrong_size = FakeOcr(size_override=(10, 10))
    jid2 = new_booklet_job(db, store, pages=1)
    assert ingest_and_read(db, store, jid2, wrong_size)[0] == JobStatus.COMPLETED
    (run2,) = runs_by_page(db, jid2)[1]
    assert run2.status == "FAILED" and (run2.error or "").startswith("size_mismatch:")


def test_a_slow_ocr_stage_keeps_its_lease_and_is_not_taken_over(env: Env) -> None:
    """The known gap from Phase 2: a stage longer than the reclaim window. 2 pages x 0.9 s against a 0.6 s window."""
    db, store = env
    fake = FakeOcr(delay_s=0.9)
    fast = LeasePolicy(heartbeat=timedelta(seconds=0.2), max_missed=3)
    ingest = new_booklet_job(db, store, pages=2)
    assert run_job(db, ingest, PIPELINES, services=services(store, fake)) == JobStatus.COMPLETED  # rendering only
    with db() as s:
        owner = s.get(ProcessingJob, ingest)
        assert owner is not None
    created = ensure_ocr_job(db, submission_of(db, ingest), owner.created_by)
    assert created is not None
    jid = created  # the machine-reading job: this is the slow one
    results: list[JobStatus | None] = []
    t = threading.Thread(target=lambda: results.append(run_job(db, jid, PIPELINES, services=services(store, fake), policy=fast)))
    t.start()
    for _ in range(100):
        if fake.calls:
            break
        threading.Event().wait(0.05)
    threading.Event().wait(1.2)  # 2 reclaim windows into the OCR stage
    assert run_job(db, jid, PIPELINES, services=services(store, fake), policy=fast) is None  # a second worker cannot take it
    t.join()
    assert results == [JobStatus.COMPLETED] and fake.calls == 2  # not read twice


def test_ocr_tables_are_append_only(env: Env) -> None:
    db, store = env
    jid = new_booklet_job(db, store, pages=1)
    assert ingest_and_read(db, store, jid, FakeOcr())[0] == JobStatus.COMPLETED
    for sql in (
        "UPDATE ocr_runs SET status = 'OK'",
        "DELETE FROM ocr_runs",
        "UPDATE ocr_lines SET text = 'x'",
        "DELETE FROM ocr_lines",
    ):
        with pytest.raises(DBAPIError, match="append-only"), db() as s, s.begin():
            s.execute(text(sql))
    assert update  # (imported for readability of the statements above)


def test_config_hash_is_canonical_and_sensitive() -> None:
    a = {"libraries": {"x": "1", "y": "2"}, "models": {"det": {"name": "d", "sha256": {"f": "1"}}}}
    b = {"models": {"det": {"sha256": {"f": "1"}, "name": "d"}}, "libraries": {"y": "2", "x": "1"}}
    assert config_hash(a) == config_hash(b) and len(config_hash(a)) == 64
    changed = {"libraries": a["libraries"], "models": {"det": {"name": "d", "sha256": {"f": "2"}}}}
    assert config_hash(changed) != config_hash(a)
