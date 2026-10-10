"""Machine-reading ("OCR assist") endpoints (D28). Display data only.

This module and everything it imports is OFF LIMITS to the grading path: nothing in grademind_core.scoring / evaluation /
grading or in the grading routes may import it (import-linter contract "OCR text never reaches a verdict").
"""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, queue_dep, require_roles, store_dep
from grademind_api.errors import ApiError
from grademind_api.queue import JobQueue
from grademind_api.routes.exams import visible_exam
from grademind_api.routes.submissions import enqueue_after_commit, visible_submission
from grademind_core.db.models import AnswerRegion, AuditLog, Exam, JobStatus, Page, ProcessingJob, Role, Submission
from grademind_core.db.ocr_models import LineCorrection, OcrLine, OcrRun
from grademind_core.jobs import OCR_RETRY_KIND
from grademind_core.line_corrections import InvalidLineTextError, chain, clean_line_text, edit_ops, heads
from grademind_core.ocr_geometry import LineGeom, lines_in_region
from grademind_core.pdf import PdfError, crop_line
from grademind_core.security import Principal
from grademind_core.storage import ObjectKind, ObjectNotFoundError, ObjectStore

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
    text: str  # what to SHOW: the examiner's correction if there is one, else the machine's text. Student text: DATA (I5).
    original_text: str  # what the machine read, never overwritten
    corrected: bool
    correction_id: uuid.UUID | None  # the current correction; send it back as expected_correction_id when editing again
    score: float | None
    low_confidence: bool  # never set on a line an examiner has corrected
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
    corrections = heads(db, [uuid.UUID(k) for k in texts])
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
            fix = corrections.get(row.id)
            lines.append(
                MachineLine(
                    id=row.id,
                    text=fix.corrected_text if fix else row.text,
                    original_text=row.text,
                    corrected=fix is not None,
                    correction_id=fix.id if fix else None,
                    score=row.score,
                    low_confidence=fix is None and (row.score is None or row.score < LOW_CONFIDENCE_BELOW),
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


# ---------------------------------------------------------------------------------------------- examiner line correction (3.4)

PREPROCESSING_VERSION = (
    "none"  # D18: page characterisation is on, image transforms are off, so the engine saw the page as rendered
)


class CorrectionIn(BaseModel):
    text: str = Field(max_length=4000)  # the line as the examiner reads it (validated more strictly on the server)
    expected_correction_id: uuid.UUID | None = None  # the correction the examiner SAW (null = none yet): optimistic concurrency


class LineState(BaseModel):
    id: uuid.UUID
    text: str
    original_text: str
    corrected: bool
    correction_id: uuid.UUID | None


class CorrectionRow(BaseModel):
    id: uuid.UUID
    created_at: datetime
    examiner_id: uuid.UUID
    corrected_text: str
    supersedes_id: uuid.UUID | None


class LineHistory(BaseModel):
    line_id: uuid.UUID
    original_text: str
    corrections: list[CorrectionRow]  # oldest first; the last one is the current correction


def _line_of(db: Session, sub: Submission, line_id: uuid.UUID) -> tuple[OcrLine, OcrRun, Page]:
    row = db.execute(
        select(OcrLine, OcrRun, Page)
        .join(OcrRun, OcrRun.id == OcrLine.ocr_run_id)
        .join(Page, Page.id == OcrRun.page_id)
        .where(OcrLine.id == line_id, Page.submission_id == sub.id, OcrRun.status == "OK")
    ).first()
    if row is None:
        raise ApiError(404, "not_found", "Line not found.")
    return row[0], row[1], row[2]


@router.put("/submissions/{submission_id}/ocr-lines/{line_id}/correction", response_model=LineState)
def correct_line(
    submission_id: uuid.UUID,
    line_id: uuid.UUID,
    body: CorrectionIn,
    request: Request,
    p: Principal = Depends(require_roles(*GRADERS)),
    db: Session = Depends(db_dep),
    store: ObjectStore = Depends(store_dep),
) -> LineState:
    """Record the examiner's reading of one machine-read line as labelled data (D19).

    Append-only: the OCR line and the page image are never touched; a correction of a correction is a new row. The crop of the
    line, the provenance and an audit row are written in the same transaction as the correction."""
    sub = visible_submission(db, p, submission_id)
    line, run, page = _line_of(db, sub, line_id)
    try:
        text = clean_line_text(body.text)
    except InvalidLineTextError as e:
        raise ApiError(422, e.code, e.message) from e
    head = heads(db, [line.id]).get(line.id)
    if (head.id if head else None) != body.expected_correction_id:
        raise ApiError(409, "line_changed", "Someone changed this line since you opened it. Reload and try again.")
    if text == (head.corrected_text if head else line.text):
        raise ApiError(422, "no_change", "That is already how this line reads.")
    try:
        crop, crop_box = crop_line(store.get_bytes(page.object_key), (line.bbox[0], line.bbox[1], line.bbox[2], line.bbox[3]))
    except (PdfError, ObjectNotFoundError) as e:
        raise ApiError(
            503, "crop_failed", "The line image could not be saved, so the correction was not recorded. Try again."
        ) from e
    crop_key = store.put(ObjectKind.LINE_CROP, io.BytesIO(crop), len(crop), "image/jpeg")
    exam = db.get(Exam, sub.exam_id)
    assert exam is not None
    row = LineCorrection(
        examiner_id=p.user_id,
        exam_id=sub.exam_id,
        submission_id=sub.id,
        page_id=page.id,
        ocr_line_id=line.id,
        subject=exam.subject,
        crop_object_key=crop_key,
        crop_sha256=hashlib.sha256(crop).hexdigest(),
        crop_bbox=crop_box,
        crop_polygon=line.polygon,
        page_image_sha256=page.sha256,
        preprocessing_version=PREPROCESSING_VERSION,
        ocr_provider=run.provider,
        ocr_model_names=run.model_names,
        ocr_weights_sha256=run.weights_sha256,
        ocr_text=line.text,  # the ORIGINAL machine reading, whatever an earlier correction said
        corrected_text=text,
        edit_ops=edit_ops(line.text, text),
        consent_scope=sub.consent_scope,
        supersedes_id=head.id if head else None,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as e:  # a concurrent correction took the same place in the chain
        db.rollback()
        raise ApiError(409, "line_changed", "Someone changed this line since you opened it. Reload and try again.") from e
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action="line.correct",
            entity_type="line_correction",
            entity_id=str(row.id),
            request_id=request.state.request_id,
            # ids and counts only: the text itself is student data and already lives, once, in line_corrections
            details={
                "exam_id": str(sub.exam_id),
                "submission_id": str(sub.id),
                "ocr_line_id": str(line.id),
                "supersedes_id": str(head.id) if head else None,
                "ops": len(row.edit_ops),
            },
        )
    )
    db.commit()
    return LineState(id=line.id, text=text, original_text=line.text, corrected=True, correction_id=row.id)


@router.get("/submissions/{submission_id}/ocr-lines/{line_id}/corrections", response_model=LineHistory)
def line_history(
    submission_id: uuid.UUID, line_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)
) -> LineHistory:
    sub = visible_submission(db, p, submission_id)
    line, _, _ = _line_of(db, sub, line_id)
    return LineHistory(
        line_id=line.id,
        original_text=line.text,
        corrections=[
            CorrectionRow(
                id=c.id,
                created_at=c.created_at,
                examiner_id=c.examiner_id,
                corrected_text=c.corrected_text,
                supersedes_id=c.supersedes_id,
            )
            for c in chain(db, line.id)
        ],
    )
