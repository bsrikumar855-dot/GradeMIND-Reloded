"""Pilot accuracy sample (D31.2): the owner transcribes a random sample of machine-read lines, and a script compares them with
the machine.

Why: the corrections table only holds lines an examiner CHANGED, so it cannot give the machine's real accuracy (see
pilot_stats.py). A random sample of ALL the lines the machine showed examiners, transcribed by the owner without seeing the
machine's text, can. The same sample and transcriptions are the D23 benchmark data.

The frame is the set of machine lines an examiner is actually shown: lines that lie inside a (not deleted) answer box, on
the latest successful reading of each page, in the chosen exams of the administrator's organisation. Each line is counted
once.

The sample is a folder on this machine, outside the repository (it holds crops of student handwriting):
  manifest.json        batch name, seed, frame size, sample size, when, consent counts, the warning
  crops/sNNN.jpg       one crop per sampled line, named by a sample number (no booklet, student or line id)
  machine.json         what the machine read (kept apart: the person transcribing must not see it)
  transcriptions.json  filled by scripts/sample_transcriber.py
Drawing is an administrator-only command and is audited. Nothing here touches marks, verdicts or snapshots (D19, D28).
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from grademind_core.dataset_export import ExportRefusedError, check_destination
from grademind_core.db.models import AnswerRegion, AuditLog, Exam, Page, Role, Submission, User
from grademind_core.db.ocr_models import OcrLine, OcrRun
from grademind_core.ocr_geometry import LineGeom, lines_in_region
from grademind_core.pdf import PdfError, crop_line
from grademind_core.pilot_stats import MIN_N
from grademind_core.storage import ObjectStore

SCHEMA_VERSION = 1
WARNING = (
    "These crops are student handwriting. Keep this folder on this machine, outside the repository; do not copy it to a cloud "
    "service, an email or a shared drive. It is pilot benchmark data (D23): consent (D20) decides what may leave the machine."
)


@dataclass(frozen=True)
class FrameLine:
    line_id: uuid.UUID
    page_id: uuid.UUID
    text: str
    score: float | None
    bbox: tuple[int, int, int, int]
    consent: str


def sampling_frame(db: Session, exam_ids: list[uuid.UUID] | None, org_id: uuid.UUID) -> list[FrameLine]:
    """Every machine line an examiner is shown, once, in a stable order (by line id)."""
    q = select(Exam).where(Exam.org_id == org_id)
    if exam_ids:
        q = q.where(Exam.id.in_(exam_ids))
    exams = {e.id for e in db.scalars(q)}
    if not exams:
        return []
    subs = {s.id: s for s in db.scalars(select(Submission).where(Submission.exam_id.in_(exams)))}
    if not subs:
        return []
    regions = list(
        db.scalars(select(AnswerRegion).where(AnswerRegion.submission_id.in_(subs), AnswerRegion.deleted_at.is_(None)))
    )
    page_ids = {r.page_id for r in regions}
    pages = {p.id: p for p in db.scalars(select(Page).where(Page.id.in_(page_ids)))} if page_ids else {}
    best: dict[uuid.UUID, uuid.UUID] = {}  # page -> latest successful run
    if page_ids:
        for run in db.scalars(
            select(OcrRun).where(OcrRun.page_id.in_(page_ids), OcrRun.status == "OK").order_by(OcrRun.created_at)
        ):
            best[run.page_id] = run.id
    page_of_run = {rid: pid for pid, rid in best.items()}
    lines: dict[str, OcrLine] = {}
    geoms: dict[uuid.UUID, list[LineGeom]] = {}
    if best:
        for ln in db.scalars(select(OcrLine).where(OcrLine.ocr_run_id.in_(list(best.values()))).order_by(OcrLine.line_no)):
            lines[str(ln.id)] = ln
            geoms.setdefault(page_of_run[ln.ocr_run_id], []).append(
                LineGeom(str(ln.id), (ln.bbox[0], ln.bbox[1], ln.bbox[2], ln.bbox[3]), [(pt[0], pt[1]) for pt in ln.polygon])
            )
    chosen: dict[str, FrameLine] = {}
    for r in regions:
        pg = pages[r.page_id]
        placed = lines_in_region(geoms.get(r.page_id, []), (r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3]), pg.width, pg.height)
        for pl in placed:
            row = lines[pl.line.key]
            if pl.line.key in chosen or not row.text.strip():
                continue
            chosen[pl.line.key] = FrameLine(
                row.id,
                r.page_id,
                row.text,
                row.score,
                (row.bbox[0], row.bbox[1], row.bbox[2], row.bbox[3]),
                subs[r.submission_id].consent_scope.value,
            )
    return sorted(chosen.values(), key=lambda f: str(f.line_id))


def draw(frame: list[FrameLine], n: int, seed: int) -> list[FrameLine]:
    """A simple random sample of n lines without replacement; the same frame, n and seed always give the same sample."""
    if n < MIN_N:
        raise ExportRefusedError(f"a sample needs at least {MIN_N} lines (asked for {n}).")
    if len(frame) < n:
        raise ExportRefusedError(f"only {len(frame)} machine line(s) are in the frame; a sample of {n} is not possible yet.")
    return random.Random(seed).sample(frame, n)  # noqa: S311 - a reproducible, recorded sample, not a secret


@dataclass(frozen=True)
class SampleResult:
    n: int
    frame_size: int
    out: Path
    skipped: int


def write_sample(
    sessions: sessionmaker[Session],
    store: ObjectStore,
    out: Path,
    admin_email: str,
    batch: str,
    n: int,
    seed: int,
    exam_ids: list[uuid.UUID] | None = None,
) -> SampleResult:
    with sessions() as s:
        admin = s.scalar(select(User).where(User.email == admin_email.strip().lower()))
        if admin is None or not admin.is_active or admin.role != Role.ADMIN:
            raise ExportRefusedError("Drawing a sample needs an active administrator account (--as-admin).")
        admin_id, org_id = admin.id, admin.org_id
        frame = sampling_frame(s, exam_ids, org_id)
        pages = {p.id: p for p in s.scalars(select(Page).where(Page.id.in_({f.page_id for f in frame})))} if frame else {}
        s.expunge_all()
    picked = draw(frame, n, seed)
    out = check_destination(out)
    (out / "crops").mkdir(parents=True)
    machine: dict[str, Any] = {}
    consent: dict[str, int] = {}
    skipped = 0
    page_cache: dict[uuid.UUID, bytes] = {}
    for fl in picked:
        try:
            if fl.page_id not in page_cache:
                page_cache[fl.page_id] = store.get_bytes(pages[fl.page_id].object_key)
            crop, _ = crop_line(page_cache[fl.page_id], fl.bbox)
        except (PdfError, KeyError, OSError):
            skipped += 1
            continue
        sid = f"s{len(machine) + 1:03d}"  # numbered in the random order of the draw: no booklet or line id leaves the database
        (out / "crops" / f"{sid}.jpg").write_bytes(crop)
        machine[sid] = {"text": fl.text, "score": fl.score}
        consent[fl.consent] = consent.get(fl.consent, 0) + 1
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "batch": batch,
        "drawn_at": datetime.now(UTC).isoformat(),
        "seed": seed,
        "requested": n,
        "lines": len(machine),
        "skipped_unreadable_crops": skipped,
        "frame_size": len(frame),
        "exams": len(exam_ids) if exam_ids else "all of the organisation's",
        "by_consent_scope": consent,
        "warning": WARNING,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "machine.json").write_text(json.dumps(machine, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    blank = {sid: {"text": None, "unreadable": False} for sid in machine}
    (out / "transcriptions.json").write_text(
        json.dumps({"batch": batch, "lines": blank}, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    with sessions() as s:
        s.add(
            AuditLog(
                actor_id=admin_id,
                action="pilot.sample_drawn",
                entity_type="dataset",
                entity_id=out.name[:64],
                details={"batch": batch, "lines": len(machine), "frame_size": len(frame), "seed": seed, "skipped": skipped},
            )
        )
        s.commit()
    return SampleResult(n=len(machine), frame_size=len(frame), out=out, skipped=skipped)


def load_sample(folder: Path) -> tuple[dict[str, Any], list[tuple[str, str, str]], int, int]:
    """(manifest, [(sample id, machine text, transcription)] for lines that were transcribed, unreadable count, not yet done)."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    machine = json.loads((folder / "machine.json").read_text(encoding="utf-8"))
    trans = json.loads((folder / "transcriptions.json").read_text(encoding="utf-8"))["lines"]
    done: list[tuple[str, str, str]] = []
    unreadable = pending = 0
    for sid, m in sorted(machine.items()):
        t = trans.get(sid) or {}
        if t.get("unreadable"):
            unreadable += 1
        elif isinstance(t.get("text"), str) and t["text"].strip():
            done.append((sid, m["text"], t["text"]))
        else:
            pending += 1
    return manifest, done, unreadable, pending
