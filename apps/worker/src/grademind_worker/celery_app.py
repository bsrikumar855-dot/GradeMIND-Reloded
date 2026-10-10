"""Celery entry point. Tasks are thin: all stage logic lives in grademind_core.jobs + grademind_worker.stages.

acks_late + prefetch 1: a worker that dies mid-job does not lose the message; the redelivered task resumes the job
(the claim is idempotent, and SUCCEEDED stages are reused from the cache).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from functools import lru_cache

from celery import Celery

from grademind_core.config import OcrProvider, get_settings
from grademind_core.db.session import session_factory
from grademind_core.jobs import RUN_JOB_TASK, LeasePolicy, resend_stuck, run_job
from grademind_core.logredact import install as install_log_redaction
from grademind_core.ocr_client import OcrServiceClient
from grademind_core.providers import ProviderRegistry
from grademind_core.storage import ObjectStore
from grademind_worker.stages import PIPELINES

install_log_redaction()  # D26.4
app = Celery("grademind", broker=get_settings().redis_url)
app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    # requeue sweep: re-sends jobs whose enqueue was lost or whose worker died (run with `celery worker -B`)
    beat_schedule={"requeue-sweep": {"task": "grademind.requeue_sweep", "schedule": 60.0}},
)


def _policy() -> LeasePolicy:
    s = get_settings()
    return LeasePolicy(heartbeat=timedelta(seconds=s.job_heartbeat_seconds), max_missed=s.job_max_missed_heartbeats)


@lru_cache(maxsize=1)
def _providers() -> ProviderRegistry:
    """Rule 7: OCR is reached only through the registry; a disabled provider's factory is never called."""
    s = get_settings()
    reg = ProviderRegistry(s)
    reg.register_ocr(OcrProvider.PADDLE_V6, lambda: OcrServiceClient(s.ocr_service_url, timeout_s=s.ocr_page_timeout_s))
    return reg


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
        services={"store": _store(), "providers": _providers(), "ocr_abort_after_unavailable": s.ocr_abort_after_unavailable},
        policy=_policy(),
    )
    return status.value if status else None


@app.task(name="grademind.requeue_sweep")  # type: ignore[untyped-decorator]
def requeue_sweep() -> int:
    return resend_stuck(session_factory(get_settings().database_url), lambda jid: run_job_task.delay(str(jid)), _policy())
