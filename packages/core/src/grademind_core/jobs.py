"""Job stage machine (spec §15). Generic: pipelines are ordered stages registered by the worker.

- **Owned lease with heartbeats (D26).** A claim (under `SELECT ... FOR UPDATE`) stores a fresh `lease_owner` token.
  While a stage runs, a heartbeat thread renews `heartbeat_at` every `heartbeat` interval, only while the token is still
  ours. A RUNNING job is reclaimable only after `max_missed` heartbeats were missed, so a long stage is never taken
  over while its worker is alive, and a killed worker's job is. Every write by the runner first re-locks the job row and
  checks the token, so a worker whose lease was reclaimed cannot write anything (`LeaseLostError`).
- **Duplicate delivery** of the same job is a no-op (not claimable).
- **Append-only history (I8).** Every stage execution appends STARTED, then SUCCEEDED or FAILED rows to
  `job_stage_attempts`. Nothing is edited, so the history the SSE stream replays is exactly what happened.
- **Cache / resume.** A stage's cache key is sha256(stage name, component version, input content hash). If a SUCCEEDED
  attempt with that key exists, the stage is skipped and its output reused. Retrying a failed job therefore re-runs only the
  failed stage and the stages after it, and a new component version invalidates the cache by construction.
- **Idempotent outputs (D26).** At most one SUCCEEDED attempt exists per cache key (partial unique index), so two
  concurrent runs of the same stage on the same inputs cannot both record an output; the loser reuses the winner's.
  Handlers get `ctx.idempotency_key` (the cache key) and must key any domain writes on it.
- **Failures** set the job to FAILED with `<STAGE>_FAILED: <reason>`. Expected failures raise `StageError` with a message
  safe to show to a user; anything else is recorded by exception type only, so student data never leaks into the reason.
"""

from __future__ import annotations

import dataclasses
import hashlib
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from grademind_core.db.models import JobStageAttempt, JobStatus, ProcessingJob, StageStatus

RUN_JOB_TASK = "grademind.run_job"  # the Celery task name shared by the API (producer) and the worker (consumer)
TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REVIEW_REQUIRED})


class StageError(Exception):
    """An expected stage failure. `reason` is a stable code; `message` is shown to users and must not contain student data."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason, self.message = reason, message


class LeaseLostError(Exception):
    """This worker's lease was reclaimed by another worker; it must stop and write nothing."""


@dataclass(frozen=True)
class LeasePolicy:
    heartbeat: timedelta = timedelta(seconds=30)
    max_missed: int = 4

    @property
    def window(self) -> timedelta:
        return self.heartbeat * self.max_missed


@dataclass(frozen=True)
class StageContext:
    job: ProcessingJob
    sessions: sessionmaker[Session]
    services: dict[str, Any] = field(default_factory=dict)  # e.g. {"store": ObjectStore}
    idempotency_key: str | None = None  # set per stage: key any domain writes on it


@dataclass(frozen=True)
class Stage:
    name: str
    component_version: str
    input_hash: Callable[[StageContext], str]
    run: Callable[[StageContext], str | None]  # returns an output reference; must persist its output atomically


@dataclass(frozen=True)
class Pipeline:
    kind: str
    stages: tuple[Stage, ...]


def cache_key(stage: Stage, input_hash: str) -> str:
    return hashlib.sha256(f"{stage.name}\x1f{stage.component_version}\x1f{input_hash}".encode()).hexdigest()


def _append(s: Session, job: ProcessingJob, stage: Stage, status: StageStatus, **kw: Any) -> JobStageAttempt:
    n = s.scalar(
        select(func.count())
        .select_from(JobStageAttempt)
        .where(
            JobStageAttempt.job_id == job.id, JobStageAttempt.stage == stage.name, JobStageAttempt.status == StageStatus.STARTED
        )
    )
    attempt_no = int(n or 0) + (1 if status == StageStatus.STARTED else 0)
    a = JobStageAttempt(
        job_id=job.id, stage=stage.name, attempt_no=attempt_no, status=status, component_version=stage.component_version, **kw
    )
    s.add(a)
    return a


def _last_alive(job: ProcessingJob) -> datetime:
    return job.heartbeat_at or job.updated_at


def claim(s: Session, job_id: uuid.UUID, policy: LeasePolicy, owner: uuid.UUID) -> ProcessingJob | None:
    """Lock the job row and take the lease if the job is QUEUED, or RUNNING with `max_missed` heartbeats missed."""
    job = s.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
    if job is None:
        return None
    now = datetime.now(UTC)
    stale = job.status == JobStatus.RUNNING and _last_alive(job) < now - policy.window
    if job.status != JobStatus.QUEUED and not stale:
        return None
    job.status = JobStatus.RUNNING
    job.error = None
    job.lease_owner = owner
    job.heartbeat_at = now
    return job


def _guard(s: Session, job: ProcessingJob, owner: uuid.UUID) -> None:
    """Re-lock the job row (blocks a concurrent reclaim) and check we still own it. Call before every runner write."""
    s.refresh(job, with_for_update=True)
    if job.lease_owner != owner:
        s.rollback()
        raise LeaseLostError(f"job {job.id}: lease now held by another worker")


class _Heartbeat:
    """Renews heartbeat_at every policy.heartbeat while a stage runs; notices (and records) a lost lease."""

    def __init__(self, sessions: sessionmaker[Session], job_id: uuid.UUID, owner: uuid.UUID, policy: LeasePolicy) -> None:
        self._sessions, self._job_id, self._owner, self._policy = sessions, job_id, owner, policy
        self._stop = threading.Event()
        self.lost = threading.Event()
        self.beats = 0
        self._thread = threading.Thread(target=self._loop, name=f"heartbeat-{job_id}", daemon=True)

    def _beat(self) -> bool:
        with self._sessions() as s:
            res = s.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == self._job_id, ProcessingJob.lease_owner == self._owner)
                .values(heartbeat_at=func.now())
                .execution_options(synchronize_session=False)
            )
            s.commit()
            return bool(res.rowcount)  # type: ignore[attr-defined]

    def _loop(self) -> None:
        while not self._stop.wait(self._policy.heartbeat.total_seconds()):
            try:
                if not self._beat():
                    self.lost.set()
                    return
                self.beats += 1
            except Exception:  # noqa: BLE001,S112 - a failed beat is a missed beat; the reclaim window absorbs it
                continue

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


@contextmanager
def _heartbeat(
    sessions: sessionmaker[Session], job: ProcessingJob, owner: uuid.UUID, policy: LeasePolicy
) -> Iterator[_Heartbeat]:
    with _Heartbeat(sessions, job.id, owner, policy) as hb:
        yield hb


def run_job(
    sessions: sessionmaker[Session],
    job_id: uuid.UUID,
    pipelines: dict[str, Pipeline],
    services: dict[str, Any] | None = None,
    policy: LeasePolicy | None = None,
) -> JobStatus | None:
    """Run (or resume) a job. Returns the final status, or None if the job was not claimable (duplicate delivery) or
    this worker lost its lease to another worker while running."""
    policy = policy or LeasePolicy()
    owner = uuid.uuid4()
    with sessions() as s:
        job = claim(s, job_id, policy, owner)
        if job is None:
            s.rollback()
            return None
        pipeline = pipelines.get(job.kind)
        if pipeline is None:
            job.status = JobStatus.FAILED
            job.error = f"UNKNOWN_PIPELINE_FAILED: no pipeline registered for kind {job.kind!r}"
            s.commit()
            return job.status
        s.commit()
        base = StageContext(job=job, sessions=sessions, services=services or {})
        try:
            for stage in pipeline.stages:
                try:
                    key = cache_key(stage, stage.input_hash(base))
                except StageError as e:
                    return _fail(s, job, owner, stage, e.reason, e.message)
                except Exception as e:  # noqa: BLE001 - recorded by type only (may contain student data)
                    return _fail(s, job, owner, stage, "internal_error", f"unexpected {type(e).__name__}")
                if _succeeded(s, key):
                    continue  # cached output (same inputs, same component version)
                _guard(s, job, owner)
                job.current_stage = stage.name
                _append(s, job, stage, StageStatus.STARTED, cache_key=key)
                s.commit()
                ctx = dataclasses.replace(base, idempotency_key=key)
                with _heartbeat(sessions, job, owner, policy):
                    try:
                        out = stage.run(ctx)
                    except StageError as e:
                        return _fail(s, job, owner, stage, e.reason, e.message, key)
                    except Exception as e:  # noqa: BLE001
                        return _fail(s, job, owner, stage, "internal_error", f"unexpected {type(e).__name__}", key)
                _guard(s, job, owner)
                _append(s, job, stage, StageStatus.SUCCEEDED, cache_key=key, output_ref=out)
                try:
                    s.commit()
                except IntegrityError:
                    s.rollback()  # a concurrent run on the same inputs recorded this output first: reuse it
                    if not _succeeded(s, key):
                        raise
            _guard(s, job, owner)
            job.status = JobStatus.COMPLETED
            job.lease_owner = None
            s.commit()
            return job.status
        except LeaseLostError:
            return None


def _succeeded(s: Session, key: str) -> bool:
    return (
        s.scalar(
            select(JobStageAttempt.id).where(JobStageAttempt.cache_key == key, JobStageAttempt.status == StageStatus.SUCCEEDED)
        )
        is not None
    )


def _fail(
    s: Session, job: ProcessingJob, owner: uuid.UUID, stage: Stage, reason: str, message: str, key: str | None = None
) -> JobStatus:
    s.rollback()
    _guard(s, job, owner)
    _append(s, job, stage, StageStatus.FAILED, cache_key=key, error=f"{reason}: {message}")
    job.status = JobStatus.FAILED
    job.current_stage = stage.name
    job.error = f"{stage.name}_FAILED: {message}"
    job.lease_owner = None
    s.commit()
    return job.status


def resumable_jobs(s: Session, queued_grace: timedelta, policy: LeasePolicy) -> list[uuid.UUID]:
    """Jobs to re-send: QUEUED for longer than `queued_grace` (the enqueue was lost, e.g. a broker outage) or RUNNING with
    `max_missed` heartbeats missed (the worker died). Re-sending is safe because the claim is idempotent."""
    now = datetime.now(UTC)
    alive = func.coalesce(ProcessingJob.heartbeat_at, ProcessingJob.updated_at)
    q = select(ProcessingJob.id).where(
        ((ProcessingJob.status == JobStatus.QUEUED) & (ProcessingJob.updated_at < now - queued_grace))
        | ((ProcessingJob.status == JobStatus.RUNNING) & (alive < now - policy.window))
    )
    return list(s.scalars(q))


def resend_stuck(
    sessions: sessionmaker[Session],
    enqueue: Callable[[uuid.UUID], None],
    policy: LeasePolicy,
    queued_grace: timedelta = timedelta(seconds=120),
    limit: int = 100,
) -> int:
    """The sweeper (3.0c): hand jobs that are stuck QUEUED (the enqueue was lost) or whose worker died to `enqueue`.
    Safe to run on a schedule and from several processes: re-sending is harmless because the claim is idempotent
    (a job that is already running or finished is simply not claimable). Returns how many were re-sent."""
    with sessions() as s:
        ids = resumable_jobs(s, queued_grace, policy)[:limit]
    for jid in ids:
        enqueue(jid)
    return len(ids)


def request_retry(s: Session, job: ProcessingJob) -> bool:
    """FAILED -> QUEUED. The caller commits and enqueues. Returns False if the job is not in a retryable state."""
    if job.status != JobStatus.FAILED:
        return False
    job.status = JobStatus.QUEUED
    job.error = None  # the failure stays in the append-only attempt log
    return True
