"""Enqueueing jobs for the worker. The API never runs pipeline work inside a request (spec §4)."""

from __future__ import annotations

import uuid
from typing import Protocol

from celery import Celery

from grademind_core.jobs import RUN_JOB_TASK


class JobQueue(Protocol):
    def enqueue(self, job_id: uuid.UUID) -> None: ...


class CeleryQueue:
    def __init__(self, broker_url: str) -> None:
        self._app = Celery("grademind-api", broker=broker_url)

    def enqueue(self, job_id: uuid.UUID) -> None:
        self._app.send_task(RUN_JOB_TASK, args=[str(job_id)])
