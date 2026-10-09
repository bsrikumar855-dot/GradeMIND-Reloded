"""Celery entry point. Tasks are thin: all stage logic lives in grademind_core.jobs + grademind_worker.stages.

acks_late + prefetch 1: a worker that dies mid-job does not lose the message; the redelivered task resumes the job
(the claim is idempotent, and SUCCEEDED stages are reused from the cache).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from functools import lru_cache

from celery import Celery

from grademind_core.config import get_settings
from grademind_core.db.session import session_factory
from grademind_core.jobs import RUN_JOB_TASK, run_job
from grademind_core.storage import ObjectStore
from grademind_worker.stages import PIPELINES

app = Celery("grademind", broker=get_settings().redis_url)
app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
)


@lru_cache(maxsize=1)
def _store() -> ObjectStore:
    return ObjectStore(get_settings())


@app.task(name=RUN_JOB_TASK)  # type: ignore[untyped-decorator]
def run_job_task(job_id: str) -> str | None:
    s = get_settings()
    status = run_job(
        session_factory(s.database_url),
        uuid.UUID(job_id),
        PIPELINES,
        services={"store": _store()},
        lease=timedelta(seconds=s.job_lease_seconds),
    )
    return status.value if status else None
