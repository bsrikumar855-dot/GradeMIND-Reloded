"""Job stage machine (spec §15). Generic: pipelines are ordered stages registered by the worker.

- **Idempotent claim.** A job is claimed under `SELECT ... FOR UPDATE`. A duplicate delivery of the same job is a no-op,
  and a RUNNING job whose lease expired (worker crash) can be claimed again and resumed.
- **Append-only history (I8).** Every stage execution appends STARTED, then SUCCEEDED or FAILED rows to
  `job_stage_attempts`. Nothing is edited, so the history the SSE stream replays is exactly what happened.
- **Cache / resume.** A stage's cache key is sha256(stage name, component version, input content hash). If a SUCCEEDED
  attempt with that key exists, the stage is skipped and its output reused. Retrying a failed job therefore re-runs only the
  failed stage and the stages after it, and a new component version invalidates the cache by construction.
- **Failures** set the job to FAILED with `<STAGE>_FAILED: <reason>`. Expected failures raise `StageError` with a message
  safe to show to a user; anything else is recorded by exception type only, so student data never leaks into the reason.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from grademind_core.db.models import JobStageAttempt, JobStatus, ProcessingJob, StageStatus

RUN_JOB_TASK = "grademind.run_job"  # the Celery task name shared by the API (producer) and the worker (consumer)
TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REVIEW_REQUIRED})


class StageError(Exception):
    """An expected stage failure. `reason` is a stable code; `message` is shown to users and must not contain student data."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason, self.message = reason, message


@dataclass(frozen=True)
class StageContext:
    job: ProcessingJob
    sessions: sessionmaker[Session]
    services: dict[str, Any] = field(default_factory=dict)  # e.g. {"store": ObjectStore}


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


def claim(s: Session, job_id: uuid.UUID, lease: timedelta) -> ProcessingJob | None:
    """Lock the job row and mark it RUNNING if it is QUEUED, or RUNNING with an expired lease. Otherwise return None."""
    job = s.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
    if job is None:
        return None
    stale = job.status == JobStatus.RUNNING and job.updated_at < datetime.now(UTC) - lease
    if job.status != JobStatus.QUEUED and not stale:
        return None
    job.status = JobStatus.RUNNING
    job.error = None
    job.updated_at = datetime.now(UTC)  # renews the lease
    return job


def run_job(
    sessions: sessionmaker[Session],
    job_id: uuid.UUID,
    pipelines: dict[str, Pipeline],
    services: dict[str, Any] | None = None,
    lease: timedelta = timedelta(minutes=15),
) -> JobStatus | None:
    """Run (or resume) a job. Returns the final status, or None if the job was not claimable (duplicate delivery)."""
    with sessions() as s:
        job = claim(s, job_id, lease)
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
        ctx = StageContext(job=job, sessions=sessions, services=services or {})
        for stage in pipeline.stages:
            try:
                key = cache_key(stage, stage.input_hash(ctx))
            except StageError as e:
                return _fail(s, job, stage, e.reason, e.message)
            except Exception as e:  # noqa: BLE001 - recorded by type only (may contain student data)
                return _fail(s, job, stage, "internal_error", f"unexpected {type(e).__name__}")
            done = s.scalar(
                select(JobStageAttempt).where(JobStageAttempt.cache_key == key, JobStageAttempt.status == StageStatus.SUCCEEDED)
            )
            if done is not None:
                continue  # cached output (same inputs, same component version)
            job.current_stage = stage.name
            job.updated_at = datetime.now(UTC)
            _append(s, job, stage, StageStatus.STARTED, cache_key=key)
            s.commit()
            try:
                out = stage.run(ctx)
            except StageError as e:
                return _fail(s, job, stage, e.reason, e.message, key)
            except Exception as e:  # noqa: BLE001
                return _fail(s, job, stage, "internal_error", f"unexpected {type(e).__name__}", key)
            _append(s, job, stage, StageStatus.SUCCEEDED, cache_key=key, output_ref=out)
            job.updated_at = datetime.now(UTC)
            s.commit()
        job.status = JobStatus.COMPLETED
        s.commit()
        return job.status


def _fail(s: Session, job: ProcessingJob, stage: Stage, reason: str, message: str, key: str | None = None) -> JobStatus:
    s.rollback()
    _append(s, job, stage, StageStatus.FAILED, cache_key=key, error=f"{reason}: {message}")
    job.status = JobStatus.FAILED
    job.current_stage = stage.name
    job.error = f"{stage.name}_FAILED: {message}"
    s.commit()
    return job.status


def request_retry(s: Session, job: ProcessingJob) -> bool:
    """FAILED -> QUEUED. The caller commits and enqueues. Returns False if the job is not in a retryable state."""
    if job.status != JobStatus.FAILED:
        return False
    job.status = JobStatus.QUEUED
    job.error = None  # the failure stays in the append-only attempt log
    return True
