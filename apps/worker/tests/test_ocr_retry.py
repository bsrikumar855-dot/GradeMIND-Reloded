"""4.0 (D29): machine reading runs as its own job on its own queue, and a reading that failed because the OCR service was
unavailable is retried by itself - bounded, with backoff - then shows "unread" until an examiner asks again.
Real Postgres + MinIO; the OCR service is a fake that can be killed and restarted in the middle of a booklet."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from drive import drive
from fake_ocr import FakeOcr, services
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from worker_world import needs_stack, new_booklet_job

from grademind_core.db.models import JobStageAttempt, JobStatus, ProcessingJob, StageStatus
from grademind_core.db.ocr_models import OcrRun
from grademind_core.jobs import (
    DEFAULT_QUEUE,
    OCR_KIND,
    OCR_QUEUE,
    LeasePolicy,
    RetryPolicy,
    ensure_ocr_job,
    queue_for_kind,
    resend_stuck,
    run_job,
    try_auto_retry,
)
from grademind_core.storage import ObjectStore
from grademind_worker.stages import PIPELINES

pytest_plugins = ["worker_world"]
pytestmark = needs_stack
Env = tuple[sessionmaker[Session], ObjectStore]
POLICY = LeasePolicy(heartbeat=timedelta(seconds=30), max_missed=4)
RETRY = RetryPolicy(max_retries=3, base=timedelta(seconds=30), cap=timedelta(minutes=15))


def job(db: sessionmaker[Session], jid: uuid.UUID) -> ProcessingJob:
    with db() as s:
        j = s.get(ProcessingJob, jid)
        assert j is not None
        s.expunge(j)
        return j


def set_job(db: sessionmaker[Session], jid: uuid.UUID, **values: object) -> None:
    with db() as s, s.begin():
        s.execute(update(ProcessingJob).where(ProcessingJob.id == jid).values(**values))


def sweep(db: sessionmaker[Session], retry: RetryPolicy = RETRY) -> list[tuple[uuid.UUID, str]]:
    sent: list[tuple[uuid.UUID, str]] = []
    resend_stuck(db, lambda jid, kind: sent.append((jid, kind)), POLICY, retry=retry)
    return sent


def read_booklet(db: sessionmaker[Session], store: ObjectStore, fake: FakeOcr, pages: int = 3) -> tuple[uuid.UUID, uuid.UUID]:
    """Upload-and-ingest a booklet and run its first reading with `fake`. Returns (ingest job, reading job)."""
    ingest = new_booklet_job(db, store, pages=pages)
    _status, ran = drive(db, ingest, PIPELINES, services(store, fake, abort_after=2), RETRY)
    return ran[0], ran[1]


def ok_pages(db: sessionmaker[Session], ingest: uuid.UUID) -> int:
    with db() as s:
        sub = s.get(ProcessingJob, ingest)
        assert sub is not None
        n = s.execute(
            select(OcrRun.page_id).where(OcrRun.status == "OK").where(OcrRun.page_id.in_(_page_ids(s, sub.submission_id)))
        ).all()
        return len({r[0] for r in n})


def _page_ids(s: Session, submission_id: uuid.UUID | None) -> list[uuid.UUID]:
    from grademind_core.db.models import Page

    return list(s.scalars(select(Page.id).where(Page.submission_id == submission_id)))


# ------------------------------------------------------------------------------------------------ the pure parts


def test_backoff_grows_by_four_and_is_capped() -> None:
    p = RetryPolicy(max_retries=5, base=timedelta(seconds=30), cap=timedelta(minutes=15))
    assert [p.delay(n).total_seconds() for n in range(5)] == [30, 120, 480, 900, 900]


def test_machine_reading_has_its_own_queue() -> None:
    assert queue_for_kind("ocr") == queue_for_kind("ocr_retry") == OCR_QUEUE
    assert queue_for_kind("ingest") == DEFAULT_QUEUE and OCR_QUEUE != DEFAULT_QUEUE


# ------------------------------------------------------------------------------------------------ the first reading


def test_a_rendered_booklet_gets_its_reading_job_exactly_once(env: Env) -> None:
    db, store = env
    ingest = new_booklet_job(db, store, pages=2)
    assert run_job(db, ingest, PIPELINES, services=services(store, FakeOcr())) == JobStatus.COMPLETED
    assert [a for a in stage_names(db, ingest)] == ["INTAKE", "RASTERIZE"]  # rendering only: no OCR in the ingest job
    owner = job(db, ingest)
    assert owner.submission_id is not None
    first = ensure_ocr_job(db, owner.submission_id, owner.created_by)
    assert first is not None and job(db, first).kind == OCR_KIND and job(db, first).status == JobStatus.QUEUED
    assert ensure_ocr_job(db, owner.submission_id, owner.created_by) is None  # a second call (the sweeper) creates nothing


def stage_names(db: sessionmaker[Session], jid: uuid.UUID) -> list[str]:
    with db() as s:
        rows = s.scalars(
            select(JobStageAttempt.stage).where(JobStageAttempt.job_id == jid, JobStageAttempt.status == StageStatus.STARTED)
        )
        return list(rows)


# ------------------------------------------------------------------------------------------------ automatic retry


def test_service_killed_mid_booklet_then_restarted_the_retry_reads_the_rest(env: Env) -> None:
    """The OCR service dies after page 1 and comes back later: no page is read twice, nothing is lost, nobody clicks."""
    db, store = env
    fake = FakeOcr(down_from=2)  # page 1 reads, then the service is gone
    ingest, reading = read_booklet(db, store, fake, pages=3)
    r = job(db, reading)
    assert r.status == JobStatus.QUEUED and r.retry_count == 1  # failed "unavailable" -> queued again by itself
    assert r.next_attempt_at is not None and r.next_attempt_at > datetime.now(UTC) + timedelta(seconds=20)  # backoff ~30 s
    assert ok_pages(db, ingest) == 1 and job(db, ingest).status == JobStatus.COMPLETED  # grading was never affected

    assert sweep(db) == []  # not before its time: the sweeper respects the backoff

    set_job(db, reading, next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))  # the 30 s have passed
    assert sweep(db) == [(reading, OCR_KIND)]  # sent to the OCR queue
    assert sweep(db) == []  # and not sent again every minute while it waits in the queue

    fake.down_from, fake.calls = None, 0  # the service has been restarted
    assert run_job(db, reading, PIPELINES, services=services(store, fake)) == JobStatus.COMPLETED
    assert fake.calls == 2  # pages 2 and 3 only; page 1 was not read again
    assert ok_pages(db, ingest) == 3 and job(db, reading).retry_count == 1


def test_retries_are_bounded_then_the_booklet_stays_unread(env: Env) -> None:
    db, store = env
    fake = FakeOcr(health_down=True)
    ingest, reading = read_booklet(db, store, fake, pages=2)
    seen = [job(db, reading).retry_count]
    for _ in range(RETRY.max_retries + 2):  # the service never comes back
        j = job(db, reading)
        if j.status == JobStatus.QUEUED:
            set_job(db, reading, next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
            assert sweep(db) == [(reading, OCR_KIND)]
            status, _ = drive(db, reading, PIPELINES, services(store, fake), RETRY)
            assert status in (JobStatus.QUEUED, JobStatus.FAILED)
            seen.append(job(db, reading).retry_count)
    final = job(db, reading)
    assert seen == [1, 2, 3, 3]  # 3 automatic retries (RETRY.max_retries), then no more
    assert final.status == JobStatus.FAILED and (final.error or "").startswith("OCR_FAILED:")
    set_job(db, reading, updated_at=datetime.now(UTC) - timedelta(hours=1))
    assert sweep(db) == []  # used up: neither the sweeper nor anything else retries it again
    assert ok_pages(db, ingest) == 0 and job(db, ingest).status == JobStatus.COMPLETED


def test_only_unavailability_is_retried(env: Env) -> None:
    db, store = env
    other = ensure_other_failed(db, store, "internal_error")  # not the service being down: the same input would fail again
    assert try_auto_retry(db, other, RETRY) is None and job(db, other).status == JobStatus.FAILED
    unavailable = ensure_other_failed(db, store, "ocr_unavailable")
    assert try_auto_retry(db, unavailable, RETRY) is not None and job(db, unavailable).status == JobStatus.QUEUED
    assert try_auto_retry(db, unavailable, RETRY) is None  # QUEUED now: nothing to retry until it fails again


def ensure_other_failed(db: sessionmaker[Session], store: ObjectStore, reason: str) -> uuid.UUID:
    """A reading job whose last recorded failure has `reason` (written the way the runner writes it)."""
    ingest = new_booklet_job(db, store, pages=1)
    assert run_job(db, ingest, PIPELINES, services=services(store, FakeOcr())) == JobStatus.COMPLETED
    owner = job(db, ingest)
    assert owner.submission_id is not None
    jid = ensure_ocr_job(db, owner.submission_id, owner.created_by)
    assert jid is not None
    with db() as s, s.begin():
        s.add(
            JobStageAttempt(
                job_id=jid, stage="OCR", attempt_no=1, status=StageStatus.FAILED, component_version="x", error=f"{reason}: boom"
            )
        )
        s.execute(update(ProcessingJob).where(ProcessingJob.id == jid).values(status=JobStatus.FAILED, error="OCR_FAILED: boom"))
    return jid


def test_the_sweeper_also_rescues_a_failed_reading_nobody_scheduled(env: Env) -> None:
    """The worker died between the failure and scheduling the retry: the job sits FAILED. The sweeper requeues it."""
    db, store = env
    jid = ensure_other_failed(db, store, "ocr_unavailable")
    set_job(db, jid, updated_at=datetime.now(UTC) - timedelta(minutes=10))
    assert sweep(db) == [(jid, OCR_KIND)]
    j = job(db, jid)
    assert j.status == JobStatus.QUEUED and j.retry_count == 1
    # a job that failed for another reason is left alone
    other = ensure_other_failed(db, store, "internal_error")
    set_job(db, other, updated_at=datetime.now(UTC) - timedelta(minutes=10))
    assert other not in [x for x, _ in sweep(db)]


def test_the_sweeper_creates_the_reading_job_a_crashed_worker_never_made(env: Env) -> None:
    db, store = env
    ingest = new_booklet_job(db, store, pages=1)
    assert run_job(db, ingest, PIPELINES, services=services(store, FakeOcr())) == JobStatus.COMPLETED
    # the worker died right after the ingest job completed: no reading job exists
    set_job(db, ingest, updated_at=datetime.now(UTC) - timedelta(minutes=10))
    sent = sweep(db)
    made = [jid for jid, kind in sent if kind == OCR_KIND and job(db, jid).submission_id == job(db, ingest).submission_id]
    assert len(made) == 1
    assert [jid for jid, _ in sweep(db)].count(made[0]) == 0  # and only once


# ------------------------------------------------------------------------------------------------ one reading at a time


def test_two_readings_of_one_booklet_can_never_be_active_together(env: Env) -> None:
    db, store = env
    failed = ensure_other_failed(db, store, "ocr_unavailable")  # a reading that failed and could be retried automatically
    owner = job(db, failed)
    assert owner.submission_id is not None
    with db() as s, s.begin():  # ...but the examiner asked for a re-read first: that job is the active reading now
        s.add(ProcessingJob(kind="ocr_retry", submission_id=owner.submission_id, created_by=owner.created_by))
    assert try_auto_retry(db, failed, RETRY) is None  # the automatic retry steps aside instead of reading the booklet twice
    assert job(db, failed).status == JobStatus.FAILED and job(db, failed).retry_count == 0
    with pytest.raises(IntegrityError), db() as s, s.begin():  # and the database itself refuses a second active reading
        s.add(ProcessingJob(kind="ocr", submission_id=owner.submission_id, created_by=owner.created_by))
