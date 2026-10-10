"""Machine-reading ("OCR assist") endpoints (D28). Display data only.

This module and everything it imports is OFF LIMITS to the grading path: nothing in grademind_core.scoring / evaluation /
grading or in the grading routes may import it (import-linter contract "OCR text never reaches a verdict").
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, queue_dep, require_roles
from grademind_api.errors import ApiError
from grademind_api.queue import JobQueue
from grademind_api.routes.exams import visible_exam
from grademind_api.routes.submissions import enqueue_after_commit, visible_submission
from grademind_core.db.models import AuditLog, JobStatus, Page, ProcessingJob, Role, Submission
from grademind_core.db.ocr_models import OcrRun
from grademind_core.jobs import OCR_RETRY_KIND
from grademind_core.security import Principal

router = APIRouter(tags=["ocr"])
GRADERS = (Role.ADMIN, Role.TEACHER, Role.EXAMINER)


class OcrSummaryRow(BaseModel):
    submission_id: uuid.UUID
    pages: int
    pages_read: int  # pages with a successful machine reading
    pages_failed: int  # pages whose only attempts failed: they simply have no machine text


@router.get("/exams/{exam_id}/ocr-summary", response_model=list[OcrSummaryRow])
def ocr_summary(exam_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> list[OcrSummaryRow]:
    visible_exam(db, p, exam_id)
    subs = list(db.scalars(select(Submission.id).where(Submission.exam_id == exam_id)))
    pages: dict[uuid.UUID, list[uuid.UUID]] = {}
    for pid, sid in db.execute(select(Page.id, Page.submission_id).where(Page.submission_id.in_(subs))):
        pages.setdefault(sid, []).append(pid)
    ok: set[uuid.UUID] = set()
    bad: set[uuid.UUID] = set()
    for page_id, status in db.execute(
        select(OcrRun.page_id, OcrRun.status).join(Page, Page.id == OcrRun.page_id).where(Page.submission_id.in_(subs))
    ):
        (ok if status == "OK" else bad).add(page_id)
    return [
        OcrSummaryRow(
            submission_id=sid,
            pages=len(pages.get(sid, [])),
            pages_read=sum(1 for pg in pages.get(sid, []) if pg in ok),
            pages_failed=sum(1 for pg in pages.get(sid, []) if pg in bad and pg not in ok),
        )
        for sid in subs
    ]


class RetryOut(BaseModel):
    job_id: uuid.UUID


@router.post("/submissions/{submission_id}/ocr/retry", response_model=RetryOut, status_code=202)
def retry_ocr(
    submission_id: uuid.UUID,
    request: Request,
    p: Principal = Depends(require_roles(*GRADERS)),
    db: Session = Depends(db_dep),
    queue: JobQueue = Depends(queue_dep),
) -> RetryOut:
    """Machine-read this booklet again: only pages without a successful reading are sent to the service."""
    sub = visible_submission(db, p, submission_id)
    busy = db.scalar(
        select(ProcessingJob.id).where(
            ProcessingJob.submission_id == sub.id, ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
        )
    )
    if busy is not None:
        raise ApiError(409, "already_running", "This booklet is still being processed. Try again when it has finished.")
    job = ProcessingJob(kind=OCR_RETRY_KIND, submission_id=sub.id, created_by=p.user_id)
    db.add(job)
    db.flush()
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action="ocr.retry_requested",
            entity_type="submission",
            entity_id=str(sub.id),
            request_id=request.state.request_id,
            details={"exam_id": str(sub.exam_id), "job_id": str(job.id)},
        )
    )
    db.commit()
    enqueue_after_commit(queue, job.id, request.state.request_id)
    return RetryOut(job_id=job.id)
