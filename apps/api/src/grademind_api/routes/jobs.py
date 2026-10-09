"""Jobs: status, SSE progress, retry of the failed stage (spec §15).

The SSE stream replays the append-only stage-attempt log, so a client that reconnects with `Last-Event-ID` receives
exactly the events it missed. It polls the DB (no extra broker dependency) and ends after the job reaches a terminal state.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sse_starlette.sse import EventSourceResponse

from grademind_api.deps import db_dep, principal_dep, queue_dep, require_roles, settings_dep
from grademind_api.errors import ApiError
from grademind_api.queue import JobQueue
from grademind_api.routes.exams import visible_exam
from grademind_api.routes.submissions import enqueue_after_commit
from grademind_core.config import Settings
from grademind_core.db.models import AuditLog, JobStageAttempt, JobStatus, ProcessingJob, Role, Submission
from grademind_core.jobs import TERMINAL, request_retry
from grademind_core.security import Principal

router = APIRouter(tags=["jobs"])


class StageEvent(BaseModel):
    seq: int
    stage: str
    status: str
    attempt_no: int
    component_version: str
    error: str | None
    at: datetime


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    submission_id: uuid.UUID | None
    status: JobStatus
    current_stage: str | None
    error: str | None
    updated_at: datetime
    stages: list[StageEvent]


def visible_job(db: Session, p: Principal, job_id: uuid.UUID) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    sub = db.get(Submission, job.submission_id) if job is not None and job.submission_id else None
    if job is None or sub is None:
        raise ApiError(404, "not_found", "Job not found.")
    try:
        visible_exam(db, p, sub.exam_id)
    except ApiError as e:
        raise ApiError(404, "not_found", "Job not found.") from e
    return job


def _events(db: Session, job_id: uuid.UUID) -> list[StageEvent]:
    rows = db.scalars(select(JobStageAttempt).where(JobStageAttempt.job_id == job_id).order_by(JobStageAttempt.seq))
    return [
        StageEvent(
            seq=a.seq,
            stage=a.stage,
            status=a.status.value,
            attempt_no=a.attempt_no,
            component_version=a.component_version,
            error=a.error,
            at=a.created_at,
        )
        for a in rows
    ]


def _job_out(db: Session, job: ProcessingJob) -> JobOut:
    return JobOut(
        id=job.id,
        kind=job.kind,
        submission_id=job.submission_id,
        status=job.status,
        current_stage=job.current_stage,
        error=job.error,
        updated_at=job.updated_at,
        stages=_events(db, job.id),
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> JobOut:
    return _job_out(db, visible_job(db, p, job_id))


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: uuid.UUID,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="last-event-id")] = None,
    p: Principal = Depends(principal_dep),
    settings: Settings = Depends(settings_dep),
) -> EventSourceResponse:
    sessions: sessionmaker[Session] = request.app.state.session_factory

    def snapshot() -> JobOut:
        with sessions() as db:
            return _job_out(db, visible_job(db, p, job_id))

    first = await anyio.to_thread.run_sync(snapshot)  # 404 before the stream starts if not visible
    cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    async def stream() -> AsyncIterator[dict[str, Any]]:
        nonlocal cursor
        snap: JobOut | None = first
        last_job: tuple[Any, ...] | None = None
        while True:
            if snap is None:
                snap = await anyio.to_thread.run_sync(snapshot)
            for ev in snap.stages:
                if ev.seq > cursor:
                    cursor = ev.seq
                    yield {"event": "stage", "id": str(ev.seq), "data": ev.model_dump_json()}
            state = (snap.status, snap.current_stage, snap.error)
            if state != last_job:
                last_job = state
                data = {"status": snap.status.value, "current_stage": snap.current_stage, "error": snap.error}
                yield {"event": "job", "data": json.dumps(data)}
            if snap.status in TERMINAL or await request.is_disconnected():
                return
            snap = None
            await anyio.sleep(settings.sse_poll_seconds)

    return EventSourceResponse(stream(), ping=15)


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=202)
def retry_job(
    job_id: uuid.UUID,
    request: Request,
    p: Principal = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
    db: Session = Depends(db_dep),
    queue: JobQueue = Depends(queue_dep),
) -> JobOut:
    job = visible_job(db, p, job_id)
    if not request_retry(db, job):
        raise ApiError(409, "not_retryable", "Only a failed job can be retried.")
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action="job.retry",
            entity_type="job",
            entity_id=str(job.id),
            request_id=request.state.request_id,
            details={"failed_stage": job.current_stage},
        )
    )
    db.commit()
    enqueue_after_commit(queue, job.id, request.state.request_id)
    return _job_out(db, job)
