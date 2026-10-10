"""3.0c: the sweeper re-sends jobs whose enqueue was lost (e.g. the broker was down at upload) or whose worker died, and
the re-sent job really runs to completion. Real Postgres + MinIO; the queue is a recording stand-in."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fake_ocr import services
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker
from worker_world import needs_stack, new_booklet_job, pages_of

from grademind_core.db.models import JobStatus, ProcessingJob
from grademind_core.jobs import LeasePolicy, resend_stuck, run_job
from grademind_core.storage import ObjectStore
from grademind_worker.stages import PIPELINES

pytest_plugins = ["worker_world"]
pytestmark = needs_stack
POLICY = LeasePolicy(heartbeat=timedelta(seconds=30), max_missed=4)


def age(db: sessionmaker[Session], jid: uuid.UUID, minutes: int, **values: object) -> None:
    old = datetime.now(UTC) - timedelta(minutes=minutes)
    with db() as s, s.begin():
        s.execute(update(ProcessingJob).where(ProcessingJob.id == jid).values(updated_at=old, **values))


def test_a_job_whose_enqueue_was_lost_is_resent_and_completes(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    jid = new_booklet_job(db, store, pages=2)  # the API committed the job, then the broker was down: nothing was enqueued
    sent: list[uuid.UUID] = []

    assert resend_stuck(db, sent.append, POLICY) == 0  # brand new: inside the grace period, the normal enqueue may still arrive
    age(db, jid, minutes=10)
    assert resend_stuck(db, sent.append, POLICY) == 1 and sent == [jid]

    assert run_job(db, jid, PIPELINES, services=services(store)) == JobStatus.COMPLETED  # what the re-sent task does
    assert len(pages_of(db, jid)) == 2
    assert resend_stuck(db, sent.append, POLICY) == 0 and sent == [jid]  # finished: never re-sent again


def test_only_stuck_jobs_are_resent(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    stuck_queued, fresh, live, dead, done, failed = (new_booklet_job(db, store, pages=1) for _ in range(6))
    age(db, stuck_queued, 30)
    age(db, live, 30, status=JobStatus.RUNNING, lease_owner=uuid.uuid4(), heartbeat_at=datetime.now(UTC))  # heartbeating
    age(db, dead, 30, status=JobStatus.RUNNING, lease_owner=uuid.uuid4(), heartbeat_at=datetime.now(UTC) - timedelta(minutes=30))
    age(db, done, 30, status=JobStatus.COMPLETED)
    age(db, failed, 30, status=JobStatus.FAILED)  # failed jobs wait for an explicit retry, they are not re-run by themselves
    sent: list[uuid.UUID] = []
    resend_stuck(db, sent.append, POLICY)
    assert {stuck_queued, dead} <= set(sent) and not {fresh, live, done, failed} & set(sent)


def test_resending_twice_is_harmless(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    """The sweep runs every minute until a worker picks the job up, so duplicate sends must not duplicate work."""
    db, store = env
    jid = new_booklet_job(db, store, pages=2)
    age(db, jid, 10)
    assert run_job(db, jid, PIPELINES, services=services(store)) == JobStatus.COMPLETED
    assert run_job(db, jid, PIPELINES, services=services(store)) is None  # the duplicate delivery is a no-op
    assert len(pages_of(db, jid)) == 2


def test_the_limit_bounds_one_sweep(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    ids = [new_booklet_job(db, store, pages=1) for _ in range(3)]
    for j in ids:
        age(db, j, 30)
    sent: list[uuid.UUID] = []
    assert resend_stuck(db, sent.append, POLICY, limit=2) == 2 and len(sent) == 2


def test_an_enqueue_failure_propagates_so_the_next_sweep_retries(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    jid = new_booklet_job(db, store, pages=1)
    age(db, jid, 30)

    def broken(_: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")

    with pytest.raises(ConnectionError):
        resend_stuck(db, broken, POLICY)
    sent: list[uuid.UUID] = []
    resend_stuck(db, sent.append, POLICY)  # the job is still QUEUED, so the next sweep tries again
    assert jid in sent
