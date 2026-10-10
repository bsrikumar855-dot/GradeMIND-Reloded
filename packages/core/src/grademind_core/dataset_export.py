"""Labelled-dataset export of examiner line corrections (3.5; D19, D20, D28).

OWNER-ONLY BY CONSTRUCTION: this is a command-line operation (`python -m grademind_core.cli export-corrections`) that
needs shell and database access on the machine, requires an active ADMIN account, and is recorded in the audit log. There is
deliberately NO HTTP route that returns this data.

Consent (D20) is enforced per row by the booklet's `consent_scope`:
- scope "public": only rows whose booklet has PUBLIC_RELEASE consent;
- scope "local": every row, for local benchmarks and local fine-tuning. These rows must never leave the machine.

The repository is PUBLIC, so the output directory may not be inside a git working tree unless git ignores it. The export
holds crops of student handwriting: identifiers are pseudonymous (no student reference, no email, no booklet id), but a crop
can still show whatever the student wrote, so a public-scope export needs the redaction review of D20 before publishing.

Integrity: every crop is read back and checked against its recorded sha256, and the edit operations are replayed on the
original text. A row that fails is NOT exported silently: it is listed in the manifest as skipped and the command exits non-zero.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from grademind_core.db.models import AuditLog, ConsentScope, Exam, Role, User
from grademind_core.db.ocr_models import LineCorrection
from grademind_core.line_corrections import apply_ops
from grademind_core.storage import ObjectNotFoundError, ObjectStore

SCHEMA_VERSION = 1
SCOPES = ("local", "public")
WARNING = {
    "local": "LOCAL ONLY: includes booklets WITHOUT public-release consent. This data must never leave this machine.",
    "public": "Only rows with PUBLIC_RELEASE consent. Crops show student handwriting: run the D20 redaction review first.",
}


class ExportRefusedError(Exception):
    """The export was refused before anything was written. `message` says why, in plain words."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class ExportResult:
    rows: int
    skipped: list[dict[str, str]] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.skipped


def _pseudonym(*parts: object) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _repo_root(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def check_destination(out: Path) -> Path:
    """The output directory must be new, and must not be somewhere git could commit it."""
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ExportRefusedError(f"{out} already contains files. Choose a new directory (an export never overwrites).")
    repo = _repo_root(out if out.exists() else out.parent)
    if repo is not None:
        res = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", str(repo), "check-ignore", "-q", str(out)],  # noqa: S607
            capture_output=True,
            check=False,
        )
        if res.returncode != 0:
            raise ExportRefusedError(
                f"{out} is inside the git repository {repo} and is not git-ignored. This repository is PUBLIC: write the export "
                "outside it, or into a git-ignored directory (for example datasets/)."
            )
    return out


def export_corrections(
    sessions: sessionmaker[Session],
    store: ObjectStore,
    out: Path,
    scope: str,
    admin_email: str,
    exam_id: uuid.UUID | None = None,
    include_history: bool = False,
) -> ExportResult:
    if scope not in SCOPES:
        raise ExportRefusedError(f"scope must be one of {SCOPES}")
    with sessions() as s:
        admin = s.scalar(select(User).where(User.email == admin_email.strip().lower()))
        if admin is None or not admin.is_active or admin.role != Role.ADMIN:
            raise ExportRefusedError("The dataset export needs an active administrator account (--as-admin).")
        admin_id, admin_org = admin.id, admin.org_id
    out = check_destination(out)

    with sessions() as s:
        q = select(LineCorrection).order_by(LineCorrection.created_at, LineCorrection.id)
        if exam_id is not None:
            q = q.where(LineCorrection.exam_id == exam_id)
        if scope == "public":
            q = q.where(LineCorrection.consent_scope == ConsentScope.PUBLIC_RELEASE)
        rows = list(s.scalars(q))
        # organisation boundary: an administrator exports only their own organisation's data
        orgs = {e.id: e.org_id for e in s.scalars(select(Exam).where(Exam.id.in_({r.exam_id for r in rows})))} if rows else {}
        rows = [r for r in rows if orgs.get(r.exam_id) == admin_org]
        superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
        if not include_history:
            rows = [r for r in rows if r.id not in superseded]  # the current correction of each line only
        s.expunge_all()

    out.mkdir(parents=True, exist_ok=True)
    (out / "crops").mkdir(exist_ok=True)
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    by_consent: dict[str, int] = {}
    for r in rows:
        crop, problem = _load_verified(store, r)
        if crop is None:
            skipped.append({"correction_id": str(r.id), "reason": problem or "unknown"})
            continue
        name = f"crops/{r.crop_sha256}.jpg"
        (out / name).write_bytes(crop)  # content-addressed: identical crops share one file
        by_consent[r.consent_scope.value] = by_consent.get(r.consent_scope.value, 0) + 1
        records.append(
            {
                "id": str(r.id),
                "supersedes_id": str(r.supersedes_id) if r.supersedes_id else None,
                "image": name,
                "image_sha256": r.crop_sha256,
                "image_bytes": len(crop),
                "ocr_text": r.ocr_text,
                "corrected_text": r.corrected_text,
                "edit_ops": r.edit_ops,
                "crop_bbox": r.crop_bbox,
                "crop_polygon": r.crop_polygon,
                "page_image_sha256": r.page_image_sha256,
                "preprocessing_version": r.preprocessing_version,
                "ocr": {"provider": r.ocr_provider, "model_names": r.ocr_model_names, "weights_sha256": r.ocr_weights_sha256},
                "subject": r.subject,
                "consent_scope": r.consent_scope.value,
                # pseudonyms: stable inside one exam (a split can keep a booklet together), not linkable to a person
                "submission_ref": _pseudonym("submission", r.exam_id, r.submission_id),
                "examiner_ref": _pseudonym("examiner", r.exam_id, r.examiner_id),
                "created_at": r.created_at.astimezone(UTC).isoformat(),
            }
        )
    body = "".join(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n" for rec in records)
    (out / "corrections.jsonl").write_text(body, encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": scope,
        "warning": WARNING[scope],
        "rows": len(records),
        "history_included": include_history,
        "by_consent_scope": by_consent,
        "subjects": sorted({rec["subject"] for rec in records}),
        "skipped": skipped,
        "corrections_jsonl_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with sessions() as s:
        s.add(
            AuditLog(
                actor_id=admin_id,
                action="dataset.export",
                entity_type="dataset",
                entity_id=manifest["corrections_jsonl_sha256"][:32],
                details={
                    "scope": scope,
                    "rows": len(records),
                    "skipped": len(skipped),
                    "exam_id": str(exam_id) if exam_id else None,
                    "dir": out.name,
                },
            )
        )
        s.commit()
    return ExportResult(rows=len(records), skipped=skipped, manifest=manifest)


def _load_verified(store: ObjectStore, r: LineCorrection) -> tuple[bytes | None, str | None]:
    """(crop bytes, None) when the row is exportable; otherwise (None, reason)."""
    try:
        crop = store.get_bytes(r.crop_object_key)
    except ObjectNotFoundError:
        return None, "crop_missing"
    if hashlib.sha256(crop).hexdigest() != r.crop_sha256:
        return None, "crop_hash_mismatch"
    if apply_ops(r.ocr_text, r.edit_ops) != r.corrected_text:
        return None, "edit_ops_do_not_reproduce_the_correction"
    return crop, None
