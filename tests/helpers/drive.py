"""Run a job the way the worker does: the job, then whatever follows it (the machine-reading job of a rendered booklet),
inline and in order. Automatic retries are NOT run here (the sweeper sends them when their backoff has passed)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from grademind_core.db.models import JobStatus
from grademind_core.jobs import Pipeline, RetryPolicy, run_job
from grademind_worker.followups import after_job


def drive(
    sessions: sessionmaker[Session],
    job_id: uuid.UUID,
    pipelines: dict[str, Pipeline],
    services: dict[str, Any],
    retry: RetryPolicy | None = None,
) -> tuple[JobStatus | None, list[uuid.UUID]]:
    """Returns (status of the LAST job run, ids of every job run, in order)."""
    sent: list[uuid.UUID] = []
    todo, ran, last = [job_id], [], None
    while todo:
        jid = todo.pop(0)
        sent.clear()
        last = run_job(sessions, jid, pipelines, services=services)
        ran.append(jid)
        after_job(sessions, jid, last, lambda j, _kind: sent.append(j), retry or RetryPolicy())
        todo.extend(sent)
    return last, ran
