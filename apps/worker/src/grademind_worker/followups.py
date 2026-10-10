"""What follows a finished job (4.0, D29). Kept apart from the Celery wiring so the tests drive exactly what the worker does.

- A booklet whose pages are rendered (ingest COMPLETED) gets its machine-reading job, which runs on the OCR queue.
- A machine reading that failed because the service was unavailable is queued again by itself, with backoff, a bounded
  number of times. It is NOT enqueued here: the job goes back to QUEUED with a `next_attempt_at`, and the sweeper sends it when
  that time has come, so a restart of any process in between loses nothing (and no worker sits on a delayed message).
  After the last retry the job stays FAILED and the booklet shows "unread" until an examiner asks for a re-read.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from grademind_core.db.models import JobStatus, ProcessingJob
from grademind_core.jobs import OCR_KIND, OCR_KINDS, RetryPolicy, ensure_ocr_job, try_auto_retry

log = logging.getLogger(__name__)


def after_job(
    sessions: sessionmaker[Session],
    job_id: uuid.UUID,
    status: JobStatus | None,
    send: Callable[[uuid.UUID, str], None],
    retry: RetryPolicy,
) -> None:
    with sessions() as s:
        job = s.get(ProcessingJob, job_id)
        if job is None:
            return
        kind, submission_id, created_by = job.kind, job.submission_id, job.created_by
    if status == JobStatus.COMPLETED and kind == "ingest" and submission_id is not None:
        new_id = ensure_ocr_job(sessions, submission_id, created_by)
        if new_id is not None:
            send(new_id, OCR_KIND)
    elif status == JobStatus.FAILED and kind in OCR_KINDS:
        delay = try_auto_retry(sessions, job_id, retry)
        if delay is not None:
            log.info(json.dumps({"event": "ocr.auto_retry_scheduled", "job_id": str(job_id), "in_s": delay.total_seconds()}))
