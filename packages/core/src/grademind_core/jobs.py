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

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, sessionmaker

from grademind_core.db.models import JobStageAttempt, JobStatus, ProcessingJob, StageStatus

OCR_RETRY_KIND = "ocr_retry"  # job kind: machine-read one submission again (producer: API, consumer: worker pipelines)
OCR_KIND = "ocr"  # job kind: the automatic machine reading of a booklet once its page images exist (4.0)
OCR_KINDS = frozenset({OCR_KIND, OCR_RETRY_KIND})
OCR_QUEUE = (
    "ocr"  # machine reading runs on its own queue and worker: it is slow (~12 s/page) and must never starve page rendering
)
DEFAULT_QUEUE = "celery"
# stage failure reasons that mean "the reading service was unavailable": worth retrying by itself (a page that the service
# rejected is not: the same page would be rejected again)
RETRYABLE_OCR_REASONS = ("ocr_unavailable", "ocr_incomplete")
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


def queue_for_kind(kind: str) -> str:
    """Which queue a job of this kind runs on: machine reading has its own (one reading at a time per OCR instance)."""
    return OCR_QUEUE if kind in OCR_KINDS else DEFAULT_QUEUE


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded automatic retry of a machine reading that failed because the service was unavailable (4.0, D29)."""

    max_retries: int = 4
    base: timedelta = timedelta(seconds=30)
    cap: timedelta = timedelta(minutes=15)

    def delay(self, n: int) -> timedelta:
        """Wait before retry number n (0-based): base * 4**n, never more than cap (30 s, 2 min, 8 min, 15 min, ...)."""
        wait: timedelta = self.base * (4 ** min(n, 20))  # (the exponent is clamped: a huge n must not overflow a timedelta)
        return min(self.cap, wait)


def last_failure_reason(s: Session, job_id: uuid.UUID) -> str | None:
    err = s.scalar(
        select(JobStageAttempt.error)
        .where(JobStageAttempt.job_id == job_id, JobStageAttempt.status == StageStatus.FAILED)
        .order_by(JobStageAttempt.seq.desc())
        .limit(1)
    )
    return err.split(":", 1)[0] if err else None


def try_auto_retry(
    sessions: sessionmaker[Session], job_id: uuid.UUID, policy: RetryPolicy, immediate: bool = False
) -> timedelta | None:
    """FAILED machine-reading job whose last failure was "service unavailable" -> QUEUED again, once per call, at most
    `policy.max_retries` times in all. Returns how long to wait before it should run (the caller enqueues with that
    countdown), or None if it is not retryable (wrong kind or reason, retries used up, or another reading of the booklet is
    already active). The failure stays in the append-only attempt log; only the job row is reset."""
    with sessions() as s:
        job = s.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job is None or job.kind not in OCR_KINDS or job.status != JobStatus.FAILED or job.retry_count >= policy.max_retries:
            return None
        if last_failure_reason(s, job.id) not in RETRYABLE_OCR_REASONS:
            return None
        delay = timedelta(0) if immediate else policy.delay(job.retry_count)
        job.retry_count += 1
        job.next_attempt_at = datetime.now(UTC) + delay
        job.status = JobStatus.QUEUED
        job.error = None
        try:
            s.commit()
        except IntegrityError:  # a manual re-read of this booklet is already active: that one is the reading now
            s.rollback()
            return None
        return delay


def ensure_ocr_job(sessions: sessionmaker[Session], submission_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID | None:
    """After a booklet's pages are rendered: create its (first) machine-reading job unless one exists. Returns the new job's
    id, or None when the booklet already has one. Safe to call from the worker and from the sweeper at the same time."""
    with sessions() as s:
        if s.scalar(
            select(ProcessingJob.id)
            .where(ProcessingJob.submission_id == submission_id, ProcessingJob.kind.in_(OCR_KINDS))
            .limit(1)
        ):
            return None
        job = ProcessingJob(kind=OCR_KIND, submission_id=submission_id, created_by=created_by)
        s.add(job)
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            return None
        return job.id


def resumable_jobs(s: Session, queued_grace: timedelta, policy: LeasePolicy) -> list[tuple[uuid.UUID, str]]:
    """(job id, kind) of jobs to re-send: QUEUED for longer than `queued_grace` and past any retry backoff (the enqueue was
    lost, e.g. a broker outage) or RUNNING with `max_missed` heartbeats missed (the worker died). Re-sending is safe because
    the claim is idempotent."""
    now = datetime.now(UTC)
    alive = func.coalesce(ProcessingJob.heartbeat_at, ProcessingJob.updated_at)
    q = select(ProcessingJob.id, ProcessingJob.kind).where(
        or_(
            and_(
                ProcessingJob.status == JobStatus.QUEUED,
                or_(
                    # a plain QUEUED job: re-send once the enqueue is overdue
                    and_(ProcessingJob.next_attempt_at.is_(None), ProcessingJob.updated_at < now - queued_grace),
                    # a job waiting out a retry backoff (or already re-sent once): only when that time has come
                    ProcessingJob.next_attempt_at <= now,
                ),
            ),
            and_(ProcessingJob.status == JobStatus.RUNNING, alive < now - policy.window),
        )
    )
    return [(jid, kind) for jid, kind in s.execute(q)]


def resend_stuck(
    sessions: sessionmaker[Session],
    enqueue: Callable[[uuid.UUID, str], None],
    policy: LeasePolicy,
    queued_grace: timedelta = timedelta(seconds=120),
    limit: int = 100,
    retry: RetryPolicy | None = None,
) -> int:
    """The sweeper: hand jobs that are stuck QUEUED (the enqueue was lost) or whose worker died to `enqueue(job_id, kind)`;
    also (4.0) re-queue FAILED machine readings that are due an automatic retry (a worker may have died between the failure and
    scheduling the retry), and create the machine-reading job of a booklet whose rendering finished but whose reading job was
    never created. Safe to run on a schedule and from several processes: re-sending is harmless because the claim is
    idempotent. Returns how many jobs were handed to `enqueue`."""
    retry = retry or RetryPolicy()
    now = datetime.now(UTC)
    with sessions() as s:
        due = resumable_jobs(s, queued_grace, policy)[:limit]
        failed = [
            jid
            for jid, n, updated in s.execute(
                select(ProcessingJob.id, ProcessingJob.retry_count, ProcessingJob.updated_at).where(
                    ProcessingJob.kind.in_(OCR_KINDS),
                    ProcessingJob.status == JobStatus.FAILED,
                    ProcessingJob.retry_count < retry.max_retries,
                )
            )
            if now - _as_utc(updated) >= max(queued_grace, retry.delay(n))
        ][:limit]
        other = aliased(ProcessingJob)
        unread = [
            (sub_id, user_id)
            for sub_id, user_id in s.execute(
                select(ProcessingJob.submission_id, ProcessingJob.created_by).where(
                    ProcessingJob.kind == "ingest",
                    ProcessingJob.status == JobStatus.COMPLETED,
                    ProcessingJob.updated_at < now - queued_grace,
                    ProcessingJob.submission_id.is_not(None),
                    ~select(other.id)
                    .where(other.submission_id == ProcessingJob.submission_id, other.kind.in_(OCR_KINDS))
                    .exists(),
                )
            )
            if sub_id is not None
        ][:limit]
    sent = 0
    for jid, kind in due:
        enqueue(jid, kind)
        _hold(sessions, jid, queued_grace)
        sent += 1
    for jid in failed:
        if try_auto_retry(sessions, jid, retry, immediate=True) is not None:
            enqueue(jid, OCR_KIND)
            _hold(sessions, jid, queued_grace)
            sent += 1
    for sub_id, user_id in unread:
        new_id = ensure_ocr_job(sessions, sub_id, user_id)
        if new_id is not None:
            enqueue(new_id, OCR_KIND)
            sent += 1
    return sent


def _hold(sessions: sessionmaker[Session], job_id: uuid.UUID, grace: timedelta) -> None:
    """After re-sending a QUEUED job, do not send it again for `grace`: a deep queue must not collect a duplicate message per
    sweep. (A RUNNING job is not touched; the claim, not this, protects it.)"""
    with sessions() as s:
        s.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.status == JobStatus.QUEUED)
            .values(next_attempt_at=datetime.now(UTC) + grace)
            .execution_options(synchronize_session=False)
        )
        s.commit()


def _as_utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def request_retry(s: Session, job: ProcessingJob) -> bool:
    """FAILED -> QUEUED. The caller commits and enqueues. Returns False if the job is not in a retryable state."""
    if job.status != JobStatus.FAILED:
        return False
    job.status = JobStatus.QUEUED
    job.error = None  # the failure stays in the append-only attempt log
    return True
