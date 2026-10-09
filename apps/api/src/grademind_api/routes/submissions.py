"""Submissions: validated upload into object storage (spec §16), listing, and signed-URL access.

Storage keys never leave the server; clients get a short-lived signed URL. The original filename is display metadata
only and is kept out of logs and audit details, because answer-script filenames often contain student names.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, queue_dep, require_roles, settings_dep, store_dep
from grademind_api.errors import ApiError
from grademind_api.queue import JobQueue
from grademind_api.routes.exams import visible_exam
from grademind_core.config import Settings
from grademind_core.db.models import AuditLog, ConsentScope, ProcessingJob, Role, Submission
from grademind_core.security import Principal
from grademind_core.storage import ObjectKind, ObjectStore
from grademind_core.uploads import UploadRejectedError, validate_upload

router = APIRouter(tags=["submissions"])
log = logging.getLogger("grademind.api")

MULTIPART_OVERHEAD = 64 * 1024
STUDENT_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"  # a pseudonymous reference, not a name


class SubmissionOut(BaseModel):
    id: uuid.UUID
    exam_id: uuid.UUID
    student_ref: str
    consent_scope: ConsentScope
    mime: str
    size_bytes: int
    sha256: str
    filename: str
    created_at: datetime


class SubmissionCreated(SubmissionOut):
    job_id: uuid.UUID  # the ingest job; follow it at /api/jobs/{job_id}/events


class SubmissionDetail(SubmissionOut):
    source_url: str  # short-lived signed URL; expires after signed_url_ttl_seconds
    source_url_expires_in: int


def enqueue_after_commit(queue: JobQueue, job_id: uuid.UUID, request_id: str) -> None:
    """Enqueue only after the commit, so the worker can always see the job. If the broker is down the upload still
    succeeded: the job stays QUEUED and is visible as such (re-sending stuck QUEUED jobs is not implemented yet)."""
    try:
        queue.enqueue(job_id)
    except Exception as e:  # noqa: BLE001 - logged; the job remains QUEUED
        log.warning(json.dumps({"request_id": request_id, "job_id": str(job_id), "error": f"enqueue failed: {type(e).__name__}"}))


def _out(s: Submission) -> SubmissionOut:
    return SubmissionOut(
        id=s.id,
        exam_id=s.exam_id,
        student_ref=s.student_ref,
        consent_scope=s.consent_scope,
        mime=s.source_mime,
        size_bytes=s.source_size_bytes,
        sha256=s.source_sha256,
        filename=s.source_filename,
        created_at=s.created_at,
    )


@router.post("/exams/{exam_id}/submissions", response_model=SubmissionCreated, status_code=201)
def upload_submission(
    exam_id: uuid.UUID,
    request: Request,
    file: Annotated[UploadFile, File(description="Answer booklet: PDF, PNG or JPEG")],
    student_ref: Annotated[str, Form(pattern=STUDENT_REF_PATTERN)],
    consent_scope: Annotated[ConsentScope, Form()] = ConsentScope.LOCAL_ONLY,
    p: Principal = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
    db: Session = Depends(db_dep),
    settings: Settings = Depends(settings_dep),
    store: ObjectStore = Depends(store_dep),
    queue: JobQueue = Depends(queue_dep),
) -> SubmissionCreated:
    visible_exam(db, p, exam_id)
    try:
        upload = validate_upload(file.file, file.filename, max_bytes=settings.max_upload_bytes)
    except UploadRejectedError as e:
        raise ApiError(413 if e.code == "too_large" else 422, e.code, e.message) from e
    with upload.file:
        dup = db.scalar(select(Submission).where(Submission.exam_id == exam_id, Submission.source_sha256 == upload.sha256))
        if dup is not None:
            raise ApiError(409, "duplicate_upload", "This exact file has already been uploaded for this exam.")
        key = store.put(ObjectKind.SUBMISSION_SOURCE, upload.file, upload.size, upload.mime.value)
    sub = Submission(
        exam_id=exam_id,
        student_ref=student_ref,
        consent_scope=consent_scope,
        source_object_key=key,
        source_sha256=upload.sha256,
        source_mime=upload.mime.value,
        source_size_bytes=upload.size,
        source_filename=upload.filename,
        created_by=p.user_id,
    )
    db.add(sub)
    try:
        db.flush()
    except IntegrityError as e:  # a concurrent upload of the same file won the race
        db.rollback()
        raise ApiError(409, "duplicate_upload", "This exact file has already been uploaded for this exam.") from e
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action="submission.create",
            entity_type="submission",
            entity_id=str(sub.id),
            request_id=request.state.request_id,
            details={"exam_id": str(exam_id), "sha256": upload.sha256, "mime": upload.mime.value, "size": upload.size},
        )
    )
    job = ProcessingJob(kind="ingest", submission_id=sub.id, created_by=p.user_id)
    db.add(job)
    db.commit()  # submission, audit row and job in one transaction
    enqueue_after_commit(queue, job.id, request.state.request_id)
    return SubmissionCreated(**_out(sub).model_dump(), job_id=job.id)


@router.get("/exams/{exam_id}/submissions", response_model=list[SubmissionOut])
def list_submissions(
    exam_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    p: Principal = Depends(principal_dep),
    db: Session = Depends(db_dep),
) -> list[SubmissionOut]:
    visible_exam(db, p, exam_id)
    q = (
        select(Submission)
        .where(Submission.exam_id == exam_id)
        .order_by(Submission.created_at, Submission.id)
        .limit(limit)
        .offset(offset)
    )
    return [_out(s) for s in db.scalars(q)]


@router.get("/submissions/{submission_id}", response_model=SubmissionDetail)
def get_submission(
    submission_id: uuid.UUID,
    p: Principal = Depends(principal_dep),
    db: Session = Depends(db_dep),
    settings: Settings = Depends(settings_dep),
    store: ObjectStore = Depends(store_dep),
) -> SubmissionDetail:
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise ApiError(404, "not_found", "Submission not found.")
    try:
        visible_exam(db, p, sub.exam_id)
    except ApiError as e:
        raise ApiError(404, "not_found", "Submission not found.") from e
    return SubmissionDetail(
        **_out(sub).model_dump(),
        source_url=store.signed_url(sub.source_object_key),
        source_url_expires_in=settings.signed_url_ttl_seconds,
    )
