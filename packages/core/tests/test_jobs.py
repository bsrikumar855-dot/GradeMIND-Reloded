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
from typing import Any

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import IntegrityError
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
from grademind_core.jobs import (
    LeasePolicy,
    Pipeline,
    Stage,
    StageContext,
    StageError,
    request_retry,
    resumable_jobs,
    run_job,
)

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


def test_reclaim_only_after_max_missed_heartbeats(db: sessionmaker[Session]) -> None:
    spy = Spy()
    pipes = {"test": Pipeline("test", (stage("A", spy), stage("B", spy)))}
    jid = new_job(db)
    with db() as s, s.begin():  # a worker whose last heartbeat was 5 s ago
        s.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == jid)
            .values(status=JobStatus.RUNNING, lease_owner=uuid.uuid4(), heartbeat_at=datetime.now(UTC) - timedelta(seconds=5))
        )
    live = LeasePolicy(heartbeat=timedelta(seconds=2), max_missed=3)  # window 6 s: only 2.5 heartbeats missed
    assert run_job(db, jid, pipes, policy=live) is None
    assert spy.calls == []
    dead = LeasePolicy(heartbeat=timedelta(seconds=1), max_missed=3)  # window 3 s: 5 heartbeats missed
    assert run_job(db, jid, pipes, policy=dead) == JobStatus.COMPLETED
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
        found = {jid for jid, _kind in resumable_jobs(s, queued_grace=timedelta(minutes=2), policy=LeasePolicy())}
    assert {lost, dead} <= found and not {fresh, live, done} & found


# --- D26: heartbeats, reclaim of killed workers, lost leases, idempotent outputs ---

FAST = LeasePolicy(heartbeat=timedelta(seconds=0.2), max_missed=3)  # reclaim window 0.6 s


def wait_for(cond: Any, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.05)


def test_long_stage_keeps_its_lease_and_is_not_taken_over(db: sessionmaker[Session]) -> None:
    """A stage running 2.5 s (4x the reclaim window) keeps heartbeating; a second worker cannot take the job."""
    ran: list[str] = []

    def long_stage(ctx: StageContext) -> str | None:
        ran.append("run")
        time.sleep(2.5)
        return None

    pipes = {"test": Pipeline("test", (Stage("LONG", "1", lambda c: uuid.uuid4().hex, long_stage),))}
    jid = new_job(db)
    results: list[JobStatus | None] = []
    t = threading.Thread(target=lambda: results.append(run_job(db, jid, pipes, policy=FAST)))
    t.start()
    wait_for(lambda: ran)
    claimed_at = job_of(db, jid).heartbeat_at
    time.sleep(1.5)  # 2.5 reclaim windows after the claim
    assert run_job(db, jid, pipes, policy=FAST) is None  # second worker: the lease is alive
    beat = job_of(db, jid).heartbeat_at
    assert claimed_at is not None and beat is not None and beat > claimed_at + FAST.window  # renewed while running
    t.join()
    assert results == [JobStatus.COMPLETED] and ran == ["run"]


KILLED_WORKER = """
import sys, time, uuid
from datetime import timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from grademind_core.jobs import LeasePolicy, Pipeline, Stage, run_job
db = sessionmaker(bind=create_engine(sys.argv[1]), expire_on_commit=False)
def hang(ctx):
    time.sleep(600)
pipes = {"test": Pipeline("test", (Stage("KILLME", "1", lambda c: "fixed-input-" + sys.argv[2], hang),))}
run_job(db, uuid.UUID(sys.argv[2]), pipes, policy=LeasePolicy(heartbeat=timedelta(seconds=0.2), max_missed=3))
"""


def test_killed_worker_job_is_reclaimed(db: sessionmaker[Session]) -> None:
    jid = new_job(db)
    proc = subprocess.Popen([sys.executable, "-c", KILLED_WORKER, URL or "", str(jid)])
    try:
        wait_for(lambda: [a for a in attempts(db, jid) if a[0] == "KILLME"])  # the worker is inside the stage
        time.sleep(0.5)
        pipes = {"test": Pipeline("test", (Stage("KILLME", "1", lambda c: "fixed-input-" + str(jid), lambda c: None),))}
        assert run_job(db, jid, pipes, policy=FAST) is None  # alive and heartbeating: not reclaimable
    finally:
        proc.kill()  # SIGKILL: no cleanup, no final write
        proc.wait()
    time.sleep(FAST.window.total_seconds() + 0.4)
    assert run_job(db, jid, pipes, policy=FAST) == JobStatus.COMPLETED
    assert [(st, status.value, n) for st, status, n in attempts(db, jid)] == [
        ("KILLME", "STARTED", 1),
        ("KILLME", "STARTED", 2),
        ("KILLME", "SUCCEEDED", 2),
    ]


def test_worker_that_lost_its_lease_writes_nothing(db: sessionmaker[Session]) -> None:
    ran = threading.Event()

    def slow(ctx: StageContext) -> str | None:
        ran.set()
        time.sleep(1.0)
        return "out"

    pipes = {"test": Pipeline("test", (Stage("SLOW", "1", lambda c: uuid.uuid4().hex, slow),))}
    jid = new_job(db)
    results: list[JobStatus | None] = []
    t = threading.Thread(target=lambda: results.append(run_job(db, jid, pipes, policy=FAST)))
    t.start()
    ran.wait(10)
    thief = uuid.uuid4()
    with db() as s, s.begin():  # another worker reclaimed the job meanwhile (e.g. after a long GC pause / partition)
        s.execute(update(ProcessingJob).where(ProcessingJob.id == jid).values(lease_owner=thief))
    t.join()
    assert results == [None]  # the original worker abandoned the job
    assert [status.value for _, status, _ in attempts(db, jid)] == ["STARTED"]  # no SUCCEEDED written by it
    j = job_of(db, jid)
    assert j.lease_owner == thief and j.status == JobStatus.RUNNING


def test_duplicate_runs_cannot_double_write_an_output(db: sessionmaker[Session]) -> None:
    """Two jobs on identical inputs race through the same stage: both finish, exactly one output is recorded,
    and both runs saw the same idempotency key for their domain writes."""
    sha = uuid.uuid4().hex * 2
    keys: list[str | None] = []
    barrier = threading.Barrier(2)

    def racing(ctx: StageContext) -> str | None:
        keys.append(ctx.idempotency_key)
        barrier.wait(5)  # both runs are inside the stage at the same time
        return "out/" + str(ctx.idempotency_key)

    def input_hash(ctx: StageContext) -> str:
        return sha

    pipes = {"test": Pipeline("test", (Stage("RACE", "1", input_hash, racing),))}
    j1, j2 = new_job(db, sha=sha), new_job(db, sha=sha)
    results: list[JobStatus | None] = []
    ts = [threading.Thread(target=lambda j=j: results.append(run_job(db, j, pipes))) for j in (j1, j2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results == [JobStatus.COMPLETED, JobStatus.COMPLETED]
    assert len(keys) == 2 and keys[0] == keys[1] and keys[0] is not None
    with db() as s:
        succeeded = s.scalars(
            select(JobStageAttempt).where(JobStageAttempt.cache_key == keys[0], JobStageAttempt.status == StageStatus.SUCCEEDED)
        ).all()
    assert len(succeeded) == 1


def test_database_refuses_a_second_output_for_the_same_key(db: sessionmaker[Session]) -> None:
    jid = new_job(db)
    with db() as s, s.begin():
        for _ in range(1):
            s.add(
                JobStageAttempt(
                    job_id=jid, stage="X", attempt_no=1, status=StageStatus.SUCCEEDED, cache_key="k" * 64, component_version="1"
                )
            )
    with pytest.raises(IntegrityError), db() as s, s.begin():
        s.add(
            JobStageAttempt(
                job_id=jid, stage="X", attempt_no=2, status=StageStatus.SUCCEEDED, cache_key="k" * 64, component_version="1"
            )
        )
    with db() as s, s.begin():  # STARTED / FAILED rows may repeat the key
        s.add(
            JobStageAttempt(
                job_id=jid, stage="X", attempt_no=3, status=StageStatus.FAILED, cache_key="k" * 64, component_version="1"
            )
        )
