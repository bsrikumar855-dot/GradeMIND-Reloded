"""Backup and restore of the object store, and the checks that prove a restore is whole (4.6).

The database is dumped and restored by `scripts/backup.sh` / `scripts/restore.sh` (pg_dump / pg_restore). This module does the
other half:

- `write_archive` streams every object of the bucket into a tar. Each member carries its own SHA-256 in a PAX header and a
  closing `manifest.json` lists every key with its size and hash, so a damaged member or a truncated archive is detected
  when it is read back.
- `read_archive` reads such a tar, verifies every member against its hash and the manifest, and stores the objects (under the
  same keys) in the target bucket. Anything wrong is reported, never skipped silently.
- `verify_restore` checks a restored database + bucket against each other: the schema is at the code's revision, every stored
  page image, booklet source, question paper source and line crop exists and matches the SHA-256 the database recorded for it,
  and every finalized result snapshot recomputes exactly (I12).

Order matters in a backup: the database is dumped FIRST and the objects second. Objects are never deleted, so every object the
dump refers to is present in the archive (the archive may hold a few more, uploaded in between: harmless).
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from typing import IO, Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from grademind_core.db.models import Page, PaperSource, Submission
from grademind_core.db.ocr_models import LineCorrection
from grademind_core.result_snapshots import verify_all
from grademind_core.storage import ObjectNotFoundError, ObjectStore

FORMAT = "grademind-objects-1"
PAX_SHA = "GRADEMIND.sha256"
PAX_TYPE = "GRADEMIND.content_type"
MANIFEST = "manifest.json"


class ArchiveError(Exception):
    """The archive is damaged, incomplete or not one of ours."""


def write_archive(store: ObjectStore, out: IO[bytes]) -> dict[str, Any]:
    """Stream every object into a tar on `out`. Returns the manifest that was written as the last member."""
    entries: list[dict[str, Any]] = []
    with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for key in store.list_keys():
            data = store.get_bytes(key)
            sha = hashlib.sha256(data).hexdigest()
            info = tarfile.TarInfo(f"objects/{key}")
            info.size = len(data)
            info.pax_headers = {PAX_SHA: sha, PAX_TYPE: store.content_type_of(key)}
            tar.addfile(info, io.BytesIO(data))
            entries.append({"key": key, "size": len(data), "sha256": sha})
        manifest = {"format": FORMAT, "count": len(entries), "bytes": sum(e["size"] for e in entries), "objects": entries}
        body = json.dumps(manifest, sort_keys=True).encode()
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return manifest


@dataclass
class RestoreReport:
    restored: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def read_archive(store: ObjectStore, src: IO[bytes]) -> RestoreReport:
    """Verify and restore an archive into `store`'s bucket (which should be empty or new)."""
    rep = RestoreReport()
    seen: dict[str, str] = {}
    manifest: dict[str, Any] | None = None
    try:
        with tarfile.open(fileobj=src, mode="r|") as tar:
            for member in tar:
                f = tar.extractfile(member)
                if f is None:
                    continue
                data = f.read()
                if member.name == MANIFEST:
                    manifest = json.loads(data)
                    continue
                if not member.name.startswith("objects/"):
                    rep.problems.append(f"unexpected member {member.name!r}")
                    continue
                key = member.name[len("objects/") :]
                sha = hashlib.sha256(data).hexdigest()
                if member.pax_headers.get(PAX_SHA) != sha:
                    rep.problems.append(f"{key}: content does not match its recorded SHA-256 (damaged archive)")
                    continue
                store.put_at(key, data, member.pax_headers.get(PAX_TYPE, "application/octet-stream"))
                seen[key] = sha
                rep.restored += 1
    except (tarfile.TarError, OSError, json.JSONDecodeError) as e:
        rep.problems.append(f"the archive could not be read to the end ({type(e).__name__}): it is truncated or damaged")
        return rep
    if manifest is None or manifest.get("format") != FORMAT:
        rep.problems.append("the archive has no manifest: it is truncated or not a GradeMIND object archive")
        return rep
    listed = {e["key"]: e["sha256"] for e in manifest["objects"]}
    for key in sorted(set(listed) - set(seen)):
        rep.problems.append(f"{key}: listed in the manifest but missing from the archive")
    for key in sorted(set(seen) - set(listed)):
        rep.problems.append(f"{key}: in the archive but not in the manifest")
    for key in sorted(set(listed) & set(seen)):
        if listed[key] != seen[key]:
            rep.problems.append(f"{key}: differs from the manifest")
    if manifest["count"] != len(listed):
        rep.problems.append("the manifest's own count disagrees with its list")
    return rep


# ---------------------------------------------------------------------------------------------------------- verify


@dataclass
class VerifyRestoreReport:
    revision: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    objects_checked: int = 0
    snapshots_checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


COUNTED_TABLES = (
    "users",
    "exams",
    "submissions",
    "pages",
    "answer_regions",
    "evaluations",
    "score_results",
    "result_snapshots",
    "finalization_events",
    "ocr_runs",
    "ocr_lines",
    "line_corrections",
    "audit_logs",
)


def table_counts(db: Session) -> dict[str, int]:
    return {t: int(db.scalar(text(f"SELECT count(*) FROM {t}")) or 0) for t in COUNTED_TABLES}  # noqa: S608 - fixed table names


def _check_object(store: ObjectStore, key: str, sha: str, what: str, rep: VerifyRestoreReport) -> None:
    try:
        data = store.get_bytes(key)
    except ObjectNotFoundError:
        rep.problems.append(f"{what}: object {key} is missing from the restored bucket")
        return
    rep.objects_checked += 1
    if hashlib.sha256(data).hexdigest() != sha:
        rep.problems.append(f"{what}: object {key} does not match the SHA-256 recorded in the database")


def verify_restore(
    db: Session, store: ObjectStore, expected_revision: str, compare_counts: dict[str, int] | None = None
) -> VerifyRestoreReport:
    rep = VerifyRestoreReport()
    rep.revision = db.scalar(text("SELECT version_num FROM alembic_version"))
    if rep.revision != expected_revision:
        rep.problems.append(f"the restored schema is at revision {rep.revision}, this code expects {expected_revision}")
    rep.counts = table_counts(db)
    if compare_counts is not None:
        for table, n in compare_counts.items():
            if rep.counts.get(table) != n:
                rep.problems.append(f"{table}: {rep.counts.get(table)} rows after the restore, {n} before")
    for sub in db.scalars(select(Submission)):
        _check_object(store, sub.source_object_key, sub.source_sha256, f"booklet {sub.id} source", rep)
    for src in db.scalars(select(PaperSource)):
        _check_object(store, src.object_key, src.sha256, f"question paper source {src.id}", rep)
    for page in db.scalars(select(Page)):
        _check_object(store, page.object_key, page.sha256, f"page {page.id} image", rep)
        if page.thumb_object_key and not store.exists(page.thumb_object_key):
            rep.problems.append(f"page {page.id} thumbnail: object {page.thumb_object_key} is missing from the restored bucket")
    for c in db.scalars(select(LineCorrection)):
        _check_object(store, c.crop_object_key, c.crop_sha256, f"line correction {c.id} crop", rep)
    snaps = verify_all(db)
    rep.snapshots_checked = snaps.checked
    for key, problems in snaps.failures.items():
        rep.problems.extend(f"result snapshot {key}: {p}" for p in problems)
    return rep
