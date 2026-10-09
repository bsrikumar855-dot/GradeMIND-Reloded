"""Stage machine (spec §15) against real Postgres: idempotent claim, cache/resume, retry of the failed stage only,
safe failure reasons, lease-based resume after a crash, concurrency."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from grademind_core.db.models import (
    Exam,
    JobStageAttempt,
    JobStatus,
    Organization,
    ProcessingJob,
    Role,
    StageStatus,
    Submission,
    User,
)
from grademind_core.jobs import Pipeline, Stage, StageContext, StageError, request_retry, resumable_jobs, run_job

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GRADEMIND_TEST_DATABASE_URL not set (job tests need real Postgres)")
CORE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def db() -> Iterator[sessionmaker[Session]]:
    env = {**os.environ, "GRADEMIND_DATABASE_URL": URL or ""}
    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(CORE / "alembic.ini"), *cmd], env=env, check=True, capture_output=True
        )
    engine = create_engine(URL or "")
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def new_job(db: sessionmaker[Session], kind: str = "test", sha: str | None = None) -> uuid.UUID:
    with db() as s, s.begin():
        org = Organization(name="O")
        s.add(org)
        s.flush()
        u = User(
            org_id=org.id, email=f"u{uuid.uuid4().hex[:8]}@college-one.in", display_name="u", password_hash="x", role=Role.ADMIN
        )
        s.add(u)
        s.flush()
        ex = Exam(org_id=org.id, name="E", subject="S", total_marks=Decimal(10), created_by=u.id)
        s.add(ex)
        s.flush()
        sub = Submission(
            exam_id=ex.id,
            student_ref="S1",
            source_object_key="k",
            source_sha256=sha or uuid.uuid4().hex * 2,
            source_mime="application/pdf",
            source_size_bytes=1,
            source_filename="x.pdf",
            created_by=u.id,
        )
        s.add(sub)
        s.flush()
        job = ProcessingJob(kind=kind, submission_id=sub.id, created_by=u.id)
        s.add(job)
        s.flush()
        return job.id


class Spy:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: dict[str, Exception] = {}


def stage(name: str, spy: Spy, version: str = "1") -> Stage:
    def run(ctx: StageContext) -> str | None:
        spy.calls.append(name)
        if name in spy.fail:
            raise spy.fail[name]
        return f"out/{name}"

    def input_hash(ctx: StageContext) -> str:
        with ctx.sessions() as s:
            sub = s.get(Submission, ctx.job.submission_id)
            assert sub is not None
            return sub.source_sha256

    return Stage(name=name, component_version=version, input_hash=input_hash, run=run)


def attempts(db: sessionmaker[Session], job_id: uuid.UUID) -> list[tuple[str, StageStatus, int]]:
    with db() as s:
        rows = s.scalars(select(JobStageAttempt).where(JobStageAttempt.job_id == job_id).order_by(JobStageAttempt.seq))
        return [(a.stage, a.status, a.attempt_no) for a in rows]


def job_of(db: sessionmaker[Session], job_id: uuid.UUID) -> ProcessingJob:
    with db() as s:
        j = s.get(ProcessingJob, job_id)
        assert j is not None
        return j


def test_runs_all_stages_and_logs_append_only_history(db: sessionmaker[Session]) -> None:
    spy = Spy()
    jid = new_job(db)
    assert run_job(db, jid, {"test": Pipeline("test", (stage("A", spy), stage("B", spy)))}) == JobStatus.COMPLETED
    assert spy.calls == ["A", "B"]
    S, OK = StageStatus.STARTED, StageStatus.SUCCEEDED
    assert attempts(db, jid) == [("A", S, 1), ("A", OK, 1), ("B", S, 1), ("B", OK, 1)]
    with db() as s:
        seqs = [a.seq for a in s.scalars(select(JobStageAttempt).where(JobStageAttempt.job_id == jid))]
    assert seqs == sorted(seqs) and len(set(seqs)) == 4


def test_retry_reruns_only_the_failed_stage_and_later(db: sessionmaker[Session]) -> None:
    spy = Spy()
    spy.fail["B"] = StageError("bad_scan", "Page 3 is unreadable.")
    pipes = {"test": Pipeline("test", (stage("A", spy), stage("B", spy), stage("C", spy)))}
    jid = new_job(db)
    assert run_job(db, jid, pipes) == JobStatus.FAILED
    j = job_of(db, jid)
    assert j.error == "B_FAILED: Page 3 is unreadable." and j.current_stage == "B"
    assert spy.calls == ["A", "B"]

    del spy.fail["B"]
    with db() as s:
        jj = s.get(ProcessingJob, jid)
        assert jj is not None and request_retry(s, jj)
        s.commit()
    assert run_job(db, jid, pipes) == JobStatus.COMPLETED
    assert spy.calls == ["A", "B", "B", "C"]  # A reused from the cache; B re-run; C run
    assert [(st, n) for st, status, n in attempts(db, jid) if st == "B"] == [("B", 1), ("B", 1), ("B", 2), ("B", 2)]


def test_unexpected_errors_are_recorded_by_type_only(db: sessionmaker[Session]) -> None:
    spy = Spy()
    spy.fail["A"] = ValueError("student wrote: Ravi Kumar, roll 21CS042")
    jid = new_job(db)
    assert run_job(db, jid, {"test": Pipeline("test", (stage("A", spy),))}) == JobStatus.FAILED
    j = job_of(db, jid)
    assert j.error == "A_FAILED: unexpected ValueError"
    with db() as s:
        errs = [a.error for a in s.scalars(select(JobStageAttempt).where(JobStageAttempt.job_id == jid))]
    assert not any(e and "Ravi" in e for e in errs)


def test_duplicate_delivery_is_a_no_op(db: sessionmaker[Session]) -> None:
    spy = Spy()
    pipes = {"test": Pipeline("test", (stage("A", spy),))}
    jid = new_job(db)
    assert run_job(db, jid, pipes) == JobStatus.COMPLETED
    assert run_job(db, jid, pipes) is None  # completed: not claimable
    assert spy.calls == ["A"] and len(attempts(db, jid)) == 2


def test_running_job_with_live_lease_is_not_claimed_but_expired_lease_resumes(db: sessionmaker[Session]) -> None:
    spy = Spy()
    pipes = {"test": Pipeline("test", (stage("A", spy), stage("B", spy)))}
    jid = new_job(db)
    with db() as s, s.begin():  # simulate a worker that claimed the job 5 s ago and then died
        s.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == jid)
            .values(status=JobStatus.RUNNING, updated_at=datetime.now(UTC) - timedelta(seconds=5))
        )
    assert run_job(db, jid, pipes, lease=timedelta(minutes=10)) is None  # lease still live: another worker owns it
    assert spy.calls == []
    assert run_job(db, jid, pipes, lease=timedelta(seconds=1)) == JobStatus.COMPLETED  # lease expired: resumed
    assert spy.calls == ["A", "B"]


def test_component_version_change_invalidates_the_cache(db: sessionmaker[Session]) -> None:
    sha = uuid.uuid4().hex * 2
    spy = Spy()
    j1, j2, j3 = new_job(db, sha=sha), new_job(db, sha=sha), new_job(db, sha=sha)
    assert run_job(db, j1, {"test": Pipeline("test", (stage("A", spy, "1"),))}) == JobStatus.COMPLETED
    assert run_job(db, j2, {"test": Pipeline("test", (stage("A", spy, "1"),))}) == JobStatus.COMPLETED
    assert spy.calls == ["A"]  # same content + same version: cached across jobs
    assert run_job(db, j3, {"test": Pipeline("test", (stage("A", spy, "2"),))}) == JobStatus.COMPLETED
    assert spy.calls == ["A", "A"]  # new component version: recomputed


def test_unknown_pipeline_fails_cleanly(db: sessionmaker[Session]) -> None:
    jid = new_job(db, kind="nope")
    assert run_job(db, jid, {}) == JobStatus.FAILED
    assert (job_of(db, jid).error or "").startswith("UNKNOWN_PIPELINE_FAILED")


def test_concurrent_workers_run_a_job_once(db: sessionmaker[Session]) -> None:
    ran: list[int] = []

    def slow(ctx: StageContext) -> str | None:
        ran.append(1)
        time.sleep(0.3)
        return None

    pipes = {"test": Pipeline("test", (Stage("S", "1", lambda c: uuid.uuid4().hex, slow),))}
    jid = new_job(db)
    results: list[JobStatus | None] = []
    threads = [threading.Thread(target=lambda: results.append(run_job(db, jid, pipes))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ran) == 1 and sorted(results, key=str) == sorted([JobStatus.COMPLETED, None, None, None], key=str)


def test_resumable_jobs_finds_lost_enqueues_and_dead_workers(db: sessionmaker[Session]) -> None:
    fresh, lost, dead, live, done = (new_job(db) for _ in range(5))
    old = datetime.now(UTC) - timedelta(minutes=30)
    with db() as s, s.begin():
        s.execute(update(ProcessingJob).where(ProcessingJob.id == lost).values(updated_at=old))
        s.execute(update(ProcessingJob).where(ProcessingJob.id == dead).values(status=JobStatus.RUNNING, updated_at=old))
        s.execute(update(ProcessingJob).where(ProcessingJob.id == live).values(status=JobStatus.RUNNING))
        s.execute(update(ProcessingJob).where(ProcessingJob.id == done).values(status=JobStatus.COMPLETED, updated_at=old))
    with db() as s:
        found = set(resumable_jobs(s, queued_grace=timedelta(minutes=2), lease=timedelta(minutes=15)))
    assert {lost, dead} <= found and not {fresh, live, done} & found
