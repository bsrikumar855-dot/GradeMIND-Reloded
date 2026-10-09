"""Question paper and rubric (D25 a/b): source upload, deterministic parsing, versioned drafts, approval.

A draft can always be saved (the response lists its issues, so the editor can show them all); approval requires zero
issues. APPROVED versions are immutable in the database (trigger), so a change after approval is a new version.
The policy is snapshotted into each rubric version, so every score can be recomputed exactly.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, principal_dep, require_roles, settings_dep, store_dep
from grademind_api.errors import ApiError
from grademind_api.routes.exams import visible_exam
from grademind_core.config import Settings
from grademind_core.db.models import AuditLog, Exam, PaperSource, PaperVersion, Role, RubricVersion, VersionStatus
from grademind_core.grading import Issue, Paper, Policy, Rubric, validate_paper, validate_rubric
from grademind_core.paper_parser import parse_paper
from grademind_core.pdf import PdfError, text_layer
from grademind_core.security import Principal
from grademind_core.storage import ObjectKind, ObjectStore
from grademind_core.uploads import UploadMime, UploadRejectedError, validate_upload

router = APIRouter(tags=["paper", "rubric"])
EDITORS = (Role.ADMIN, Role.TEACHER)


class VersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    status: VersionStatus
    document: dict[str, Any]
    policy: dict[str, Any] | None = None
    paper_version_id: uuid.UUID | None = None
    created_at: datetime
    approved_at: datetime | None


class IssueOut(BaseModel):
    path: str
    code: str
    message: str


class PaperState(BaseModel):
    draft: VersionOut | None
    approved: VersionOut | None
    issues: list[IssueOut]  # of the draft


class RubricState(BaseModel):
    paper: VersionOut | None  # the approved paper the rubric is written against
    draft: VersionOut | None
    approved: VersionOut | None
    issues: list[IssueOut]
    stale_draft: bool  # the draft was written against an older paper version


class ParseIn(BaseModel):
    text: str = Field(max_length=200_000)


class ParseOut(BaseModel):
    draft: dict[str, Any]
    warnings: list[str]


class SourceOut(BaseModel):
    id: uuid.UUID
    filename: str
    has_text_layer: bool
    text: str


class PaperDraftIn(BaseModel):
    document: dict[str, Any]
    source_id: uuid.UUID | None = None


class RubricDraftIn(BaseModel):
    document: dict[str, Any]
    policy: dict[str, Any] = Field(default_factory=dict)


def _issues(found: list[Issue]) -> list[IssueOut]:
    return [IssueOut(path=i.path, code=i.code, message=i.message) for i in found]


def _schema_error(e: ValidationError, root: str) -> ApiError:
    issues = [
        {"path": root + "/" + "/".join(str(p) for p in err["loc"]), "code": "schema", "message": err["msg"]} for err in e.errors()
    ]
    return ApiError(422, "invalid_document", "The document has fields that are missing or invalid.", issues)


def _out(v: PaperVersion | RubricVersion | None) -> VersionOut | None:
    if v is None:
        return None
    return VersionOut(
        id=v.id,
        version_no=v.version_no,
        status=v.status,
        document=v.document,
        policy=getattr(v, "policy", None),
        paper_version_id=getattr(v, "paper_version_id", None),
        created_at=v.created_at,
        approved_at=v.approved_at,
    )


def _latest(db: Session, model: type[PaperVersion] | type[RubricVersion], exam_id: uuid.UUID, status: VersionStatus) -> Any:
    return db.scalar(
        select(model).where(model.exam_id == exam_id, model.status == status).order_by(model.version_no.desc()).limit(1)
    )


def approved_paper(db: Session, exam_id: uuid.UUID) -> PaperVersion | None:
    v: PaperVersion | None = _latest(db, PaperVersion, exam_id, VersionStatus.APPROVED)
    return v


def approved_rubric(db: Session, exam_id: uuid.UUID) -> RubricVersion | None:
    v: RubricVersion | None = _latest(db, RubricVersion, exam_id, VersionStatus.APPROVED)
    return v


def _next_no(db: Session, model: type[PaperVersion] | type[RubricVersion], exam_id: uuid.UUID) -> int:
    return int(db.scalar(select(func.coalesce(func.max(model.version_no), 0)).where(model.exam_id == exam_id)) or 0) + 1


def _audit(
    db: Session,
    p: Principal,
    request: Request,
    exam_id: uuid.UUID,
    action: str,
    entity: str,
    entity_id: uuid.UUID,
    **details: Any,
) -> None:
    details["exam_id"] = str(exam_id)
    db.add(
        AuditLog(
            actor_id=p.user_id,
            action=action,
            entity_type=entity,
            entity_id=str(entity_id),
            request_id=request.state.request_id,
            details=details,
        )
    )


# ---------------------------------------------------------------------------------------------------------- paper


@router.post("/exams/{exam_id}/paper/source", response_model=SourceOut, status_code=201)
def upload_paper_source(
    exam_id: uuid.UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    p: Principal = Depends(require_roles(*EDITORS)),
    db: Session = Depends(db_dep),
    settings: Settings = Depends(settings_dep),
    store: ObjectStore = Depends(store_dep),
) -> SourceOut:
    visible_exam(db, p, exam_id)
    try:
        up = validate_upload(file.file, file.filename, max_bytes=settings.max_upload_bytes)
    except UploadRejectedError as e:
        raise ApiError(413 if e.code == "too_large" else 422, e.code, e.message) from e
    with up.file:
        data = up.file.read()
        text = ""
        if up.mime == UploadMime.PDF:
            try:
                text = text_layer(data)
            except PdfError as e:
                raise ApiError(422, "unreadable_pdf", str(e)) from e
        up.file.seek(0)
        key = store.put(ObjectKind.PAPER_SOURCE, up.file, up.size, up.mime.value)
    src = PaperSource(
        exam_id=exam_id,
        object_key=key,
        sha256=up.sha256,
        mime=up.mime.value,
        filename=up.filename,
        extracted_text=text,
        created_by=p.user_id,
    )
    db.add(src)
    db.flush()
    _audit(db, p, request, exam_id, "paper.source_upload", "paper_source", src.id, sha256=up.sha256, has_text_layer=bool(text))
    db.commit()
    return SourceOut(id=src.id, filename=src.filename, has_text_layer=bool(text), text=text)


@router.post("/paper/parse", response_model=ParseOut)
def parse(body: ParseIn, p: Principal = Depends(require_roles(*EDITORS))) -> ParseOut:
    r = parse_paper(body.text)
    return ParseOut(draft=r.draft, warnings=r.warnings)


@router.get("/exams/{exam_id}/paper", response_model=PaperState)
def get_paper(exam_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> PaperState:
    exam = visible_exam(db, p, exam_id)
    draft: PaperVersion | None = _latest(db, PaperVersion, exam_id, VersionStatus.DRAFT)
    issues: list[IssueOut] = []
    if draft is not None:
        issues = _issues(validate_paper(Paper.model_validate(draft.document), exam.total_marks))
    return PaperState(draft=_out(draft), approved=_out(approved_paper(db, exam_id)), issues=issues)


@router.put("/exams/{exam_id}/paper/draft", response_model=PaperState)
def save_paper_draft(
    exam_id: uuid.UUID,
    body: PaperDraftIn,
    request: Request,
    p: Principal = Depends(require_roles(*EDITORS)),
    db: Session = Depends(db_dep),
) -> PaperState:
    exam = visible_exam(db, p, exam_id)
    try:
        paper = Paper.model_validate(body.document)
    except ValidationError as e:
        raise _schema_error(e, "paper") from e
    doc = paper.model_dump(mode="json")
    draft: PaperVersion | None = _latest(db, PaperVersion, exam_id, VersionStatus.DRAFT)
    if draft is None:
        draft = PaperVersion(
            exam_id=exam_id,
            version_no=_next_no(db, PaperVersion, exam_id),
            document=doc,
            source_id=body.source_id,
            created_by=p.user_id,
        )
        db.add(draft)
    else:
        draft.document = doc
        draft.source_id = body.source_id or draft.source_id
    db.flush()
    issues = validate_paper(paper, exam.total_marks)
    _audit(
        db, p, request, exam_id, "paper.draft_save", "paper_version", draft.id, version_no=draft.version_no, issues=len(issues)
    )
    db.commit()
    return PaperState(draft=_out(draft), approved=_out(approved_paper(db, exam_id)), issues=_issues(issues))


@router.post("/exams/{exam_id}/paper/approve", response_model=VersionOut)
def approve_paper(
    exam_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*EDITORS)), db: Session = Depends(db_dep)
) -> VersionOut:
    exam = visible_exam(db, p, exam_id)
    draft: PaperVersion | None = _latest(db, PaperVersion, exam_id, VersionStatus.DRAFT)
    if draft is None:
        raise ApiError(409, "no_draft", "There is no draft to approve. Save the structure first.")
    issues = validate_paper(Paper.model_validate(draft.document), exam.total_marks)
    if issues:
        raise ApiError(409, "draft_has_issues", "Fix the listed problems before approving.", [asdict(i) for i in issues])
    draft.status = VersionStatus.APPROVED
    draft.approved_by, draft.approved_at = p.user_id, datetime.now(UTC)
    _audit(db, p, request, exam_id, "paper.approve", "paper_version", draft.id, version_no=draft.version_no)
    db.commit()
    out = _out(draft)
    assert out is not None
    return out


# --------------------------------------------------------------------------------------------------------- rubric


def _rubric_state(db: Session, exam: Exam) -> RubricState:
    paper_v = approved_paper(db, exam.id)
    draft: RubricVersion | None = _latest(db, RubricVersion, exam.id, VersionStatus.DRAFT)
    issues: list[IssueOut] = []
    stale = False
    if draft is not None and paper_v is not None:
        stale = draft.paper_version_id != paper_v.id
        paper = Paper.model_validate(paper_v.document)
        issues = _issues(validate_rubric(Rubric.model_validate(draft.document), paper, Policy.model_validate(draft.policy)))
    return RubricState(
        paper=_out(paper_v), draft=_out(draft), approved=_out(approved_rubric(db, exam.id)), issues=issues, stale_draft=stale
    )


@router.get("/exams/{exam_id}/rubric", response_model=RubricState)
def get_rubric(exam_id: uuid.UUID, p: Principal = Depends(principal_dep), db: Session = Depends(db_dep)) -> RubricState:
    return _rubric_state(db, visible_exam(db, p, exam_id))


@router.put("/exams/{exam_id}/rubric/draft", response_model=RubricState)
def save_rubric_draft(
    exam_id: uuid.UUID,
    body: RubricDraftIn,
    request: Request,
    p: Principal = Depends(require_roles(*EDITORS)),
    db: Session = Depends(db_dep),
) -> RubricState:
    exam = visible_exam(db, p, exam_id)
    paper_v = approved_paper(db, exam_id)
    if paper_v is None:
        raise ApiError(409, "paper_not_approved", "Approve the question paper structure before writing the rubric.")
    try:
        rubric = Rubric.model_validate(body.document)
    except ValidationError as e:
        raise _schema_error(e, "rubric") from e
    try:
        policy = Policy.model_validate(body.policy)
    except ValidationError as e:
        raise _schema_error(e, "policy") from e
    draft: RubricVersion | None = _latest(db, RubricVersion, exam_id, VersionStatus.DRAFT)
    doc, pol = rubric.model_dump(mode="json"), policy.model_dump(mode="json")
    if draft is None:
        draft = RubricVersion(
            exam_id=exam_id,
            paper_version_id=paper_v.id,
            version_no=_next_no(db, RubricVersion, exam_id),
            document=doc,
            policy=pol,
            created_by=p.user_id,
        )
        db.add(draft)
    else:
        draft.document, draft.policy, draft.paper_version_id = doc, pol, paper_v.id
    db.flush()
    _audit(db, p, request, exam_id, "rubric.draft_save", "rubric_version", draft.id, version_no=draft.version_no)
    db.commit()
    return _rubric_state(db, exam)


@router.post("/exams/{exam_id}/rubric/approve", response_model=VersionOut)
def approve_rubric(
    exam_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*EDITORS)), db: Session = Depends(db_dep)
) -> VersionOut:
    exam = visible_exam(db, p, exam_id)
    state = _rubric_state(db, exam)
    draft: RubricVersion | None = _latest(db, RubricVersion, exam_id, VersionStatus.DRAFT)
    if draft is None:
        raise ApiError(409, "no_draft", "There is no rubric draft to approve. Save it first.")
    if state.stale_draft:
        raise ApiError(
            409, "paper_changed", "The question paper was re-approved since this draft was saved. Save the rubric again."
        )
    if state.issues:
        raise ApiError(
            409, "draft_has_issues", "Fix the listed problems before approving.", [i.model_dump() for i in state.issues]
        )
    draft.status = VersionStatus.APPROVED
    draft.approved_by, draft.approved_at = p.user_id, datetime.now(UTC)
    _audit(
        db,
        p,
        request,
        exam_id,
        "rubric.approve",
        "rubric_version",
        draft.id,
        version_no=draft.version_no,
        paper_version_id=str(draft.paper_version_id),
    )
    db.commit()
    out = _out(draft)
    assert out is not None
    return out
