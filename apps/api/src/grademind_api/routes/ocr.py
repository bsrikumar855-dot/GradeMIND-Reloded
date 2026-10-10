"""Machine-reading ("OCR assist") endpoints (D28). Display data only.

This module and everything it imports is OFF LIMITS to the grading path: nothing in grademind_core.scoring / evaluation /
grading or in the grading routes may import it (import-linter contract "OCR text never reaches a verdict").
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, queue_dep, require_roles
from grademind_api.errors import ApiError
from grademind_api.queue import JobQueue
from grademind_api.routes.exams import visible_exam
from grademind_api.routes.submissions import enqueue_after_commit, visible_submission
from grademind_core.db.models import AnswerRegion, AuditLog, JobStatus, Page, ProcessingJob, Role, Submission
from grademind_core.db.ocr_models import OcrLine, OcrRun
from grademind_core.jobs import OCR_RETRY_KIND
from grademind_core.ocr_geometry import LineGeom, lines_in_region
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


# ---------------------------------------------------------------------------------------------- machine reading per region

# A display heuristic, NOT a calibrated probability: Phase 0b measured a best line error of about 0.43 on real handwriting and
# did not establish what a score means. It only decides which lines get a visual "check this one" mark.
LOW_CONFIDENCE_BELOW = 0.80
NOTICE = "Machine reading: it can be wrong, especially for handwriting. Always check it against the page."


class MachineLine(BaseModel):
    id: uuid.UUID
    text: str  # student text: DATA. Clients must render it as escaped text only (I5).
    score: float | None
    low_confidence: bool
    bbox: list[float]  # [x0, y0, x1, y1] as fractions of the unrotated page, the same space as answer regions
    overlap: float  # share of the line that lies inside the region (1.0 = fully inside)


class RegionReading(BaseModel):
    region_id: uuid.UUID
    qid: str
    attempt_no: int
    crossed_out: bool
    page_id: uuid.UUID
    page_no: int
    page_status: Literal["read", "failed", "unread"]  # failed / unread pages simply have no machine text
    lines: list[MachineLine]


class MachineReading(BaseModel):
    notice: str
    low_confidence_below: float
    regions: list[RegionReading]


@router.get("/submissions/{submission_id}/machine-reading", response_model=MachineReading)
def machine_reading(
    submission_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)
) -> MachineReading:
    """For every live answer region: the machine-read lines that fall inside it, in reading order. Display data only."""
    sub = visible_submission(db, p, submission_id)
    regions = list(
        db.scalars(
            select(AnswerRegion)
            .where(AnswerRegion.submission_id == sub.id, AnswerRegion.deleted_at.is_(None))
            .order_by(AnswerRegion.created_at, AnswerRegion.id)
        )
    )
    page_ids = {r.page_id for r in regions}
    pages = {pg.id: pg for pg in db.scalars(select(Page).where(Page.id.in_(page_ids)))} if page_ids else {}
    best: dict[uuid.UUID, uuid.UUID] = {}  # page -> latest successful run
    failed: set[uuid.UUID] = set()
    if page_ids:
        for run in db.scalars(select(OcrRun).where(OcrRun.page_id.in_(page_ids)).order_by(OcrRun.created_at)):
            if run.status == "OK":
                best[run.page_id] = run.id  # later runs overwrite earlier ones
            else:
                failed.add(run.page_id)
    geoms: dict[uuid.UUID, list[LineGeom]] = {}
    texts: dict[str, OcrLine] = {}
    page_of_run = {rid: pid for pid, rid in best.items()}
    if best:
        for ln in db.scalars(select(OcrLine).where(OcrLine.ocr_run_id.in_(list(best.values()))).order_by(OcrLine.line_no)):
            texts[str(ln.id)] = ln
            geoms.setdefault(page_of_run[ln.ocr_run_id], []).append(
                LineGeom(str(ln.id), (ln.bbox[0], ln.bbox[1], ln.bbox[2], ln.bbox[3]), [(pt[0], pt[1]) for pt in ln.polygon])
            )
    out: list[RegionReading] = []
    for r in regions:
        pg = pages[r.page_id]
        status: Literal["read", "failed", "unread"] = (
            "read" if r.page_id in best else ("failed" if r.page_id in failed else "unread")
        )
        placed = lines_in_region(geoms.get(r.page_id, []), (r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3]), pg.width, pg.height)
        lines = []
        for pl in placed:
            row = texts[pl.line.key]
            b = pl.line.box
            lines.append(
                MachineLine(
                    id=row.id,
                    text=row.text,
                    score=row.score,
                    low_confidence=row.score is None or row.score < LOW_CONFIDENCE_BELOW,
                    bbox=[
                        round(b[0] / pg.width, 5),
                        round(b[1] / pg.height, 5),
                        round(b[2] / pg.width, 5),
                        round(b[3] / pg.height, 5),
                    ],
                    overlap=round(pl.overlap, 4),
                )
            )
        out.append(
            RegionReading(
                region_id=r.id,
                qid=r.qid,
                attempt_no=r.attempt_no,
                crossed_out=r.crossed_out,
                page_id=r.page_id,
                page_no=pg.page_no,
                page_status=status,
                lines=lines,
            )
        )
    return MachineReading(notice=NOTICE, low_confidence_below=LOW_CONFIDENCE_BELOW, regions=out)
