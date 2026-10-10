"""Owner-only labelled-dataset export (3.5): consent per booklet, pseudonyms, integrity, destinations. Real Postgres + MinIO."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from grademind_core.cli import main
from grademind_core.config import Env, Settings, get_settings
from grademind_core.dataset_export import ExportRefusedError, check_destination, export_corrections
from grademind_core.db.models import AuditLog, ConsentScope, Exam, Organization, Page, Role, Submission, User
from grademind_core.db.ocr_models import LineCorrection
from grademind_core.line_corrections import edit_ops
from grademind_core.storage import ObjectKind, ObjectStore

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
S3 = os.environ.get("GRADEMIND_TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(not (URL and S3), reason="needs GRADEMIND_TEST_DATABASE_URL and GRADEMIND_TEST_S3_ENDPOINT")
CORE = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
BUCKET = "gm-test-export"
KEYS = os.environ.get("GRADEMIND_TEST_S3_ACCESS_KEY", ""), os.environ.get("GRADEMIND_TEST_S3_SECRET_KEY", "")
CONSENT = {"local": ConsentScope.LOCAL_ONLY, "public": ConsentScope.PUBLIC_RELEASE}


def new_store() -> ObjectStore:
    return ObjectStore(
        Settings(env=Env.TEST, s3_endpoint_url=S3 or "", s3_access_key=KEYS[0], s3_secret_key=KEYS[1], s3_bucket=BUCKET)
    )


@pytest.fixture(scope="module")
def world() -> Iterator[dict[str, Any]]:
    env = {**os.environ, "GRADEMIND_DATABASE_URL": URL or ""}
    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(CORE / "alembic.ini"), *cmd], env=env, check=True, capture_output=True
        )
    engine = create_engine(URL or "")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = new_store()
    store.ensure_bucket()
    w: dict[str, Any] = {"sessions": sessions, "store": store}
    with sessions() as s, s.begin():
        org, other = Organization(name="Org"), Organization(name="Other org")
        s.add_all([org, other])
        s.flush()
        users: dict[str, User] = {}
        for key, o, role, active in [
            ("admin", org, Role.ADMIN, True),
            ("examiner", org, Role.EXAMINER, True),
            ("gone", org, Role.ADMIN, False),
            ("admin2", other, Role.ADMIN, True),
        ]:
            u = User(org_id=o.id, email=f"{key}@college-one.in", display_name=key, password_hash="x", role=role, is_active=active)
            s.add(u)
            s.flush()
            users[key] = u
        exam = Exam(org_id=org.id, name="CIA", subject="EVS", total_marks=Decimal(10), created_by=users["admin"].id)
        s.add(exam)
        s.flush()
        w.update(exam=exam.id, users={k: v.id for k, v in users.items()})
        for tag, consent in CONSENT.items():
            sub = Submission(
                exam_id=exam.id,
                student_ref=f"ROLL-{tag}-9999",
                consent_scope=consent,
                source_object_key="k",
                source_sha256=hashlib.sha256(tag.encode()).hexdigest(),
                source_mime="application/pdf",
                source_size_bytes=1,
                source_filename=f"{tag}.pdf",
                created_by=users["admin"].id,
            )
            s.add(sub)
            s.flush()
            pg = Page(submission_id=sub.id, page_no=1, object_key="p", sha256="a" * 64, width=100, height=100)
            s.add(pg)
            s.flush()
            w[tag] = {"sub": sub.id, "page": pg.id}
    yield w
    engine.dispose()


def correction_row(w: dict[str, Any], tag: str, old: str, new: str, crop_key: str, crop_sha: str) -> LineCorrection:
    return LineCorrection(
        examiner_id=w["users"]["examiner"],
        exam_id=w["exam"],
        submission_id=w[tag]["sub"],
        page_id=w[tag]["page"],
        subject="EVS",
        crop_object_key=crop_key,
        crop_sha256=crop_sha,
        crop_bbox=[1, 2, 30, 40],
        crop_polygon=[[1, 2], [30, 2]],
        page_image_sha256="a" * 64,
        preprocessing_version="none",
        ocr_provider="paddle_v6",
        ocr_model_names={"rec": "PP-OCRv6_medium_rec"},
        ocr_weights_sha256={},
        ocr_text=old,
        corrected_text=new,
        edit_ops=edit_ops(old, new),
        consent_scope=CONSENT[tag],
    )


def add_correction(w: dict[str, Any], tag: str, old: str, new: str, supersedes: uuid.UUID | None = None) -> uuid.UUID:
    data = b"\xff\xd8\xff-crop-" + uuid.uuid4().bytes
    key = w["store"].put(ObjectKind.LINE_CROP, io.BytesIO(data), len(data), "image/jpeg")
    row = correction_row(w, tag, old, new, key, hashlib.sha256(data).hexdigest())
    row.supersedes_id = supersedes
    with w["sessions"]() as s, s.begin():
        s.add(row)
        s.flush()
        return row.id


def run(w: dict[str, Any], out: Path, scope: str, admin: str = "admin@college-one.in", **kw: Any) -> Any:
    return export_corrections(w["sessions"], w["store"], out, scope, admin, **kw)


def records(out: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (out / "corrections.jsonl").read_text(encoding="utf-8").splitlines()]


def test_consent_scope_decides_what_is_exported(world: dict[str, Any], tmp_path: Path) -> None:
    add_correction(world, "local", "divesity", "diversity")
    add_correction(world, "public", "ecosistem", "ecosystem")
    pub = run(world, tmp_path / "pub", "public")
    assert pub.ok and pub.rows == 1 and pub.manifest["by_consent_scope"] == {"public_release": 1}
    assert [r["corrected_text"] for r in records(tmp_path / "pub")] == ["ecosystem"]
    loc = run(world, tmp_path / "loc", "local")
    assert loc.rows == 2 and loc.manifest["by_consent_scope"] == {"local_only": 1, "public_release": 1}
    assert "must never leave this machine" in loc.manifest["warning"] and loc.manifest["scope"] == "local"
    # a public export can never contain a local-only crop: assert on the FILES, not just the counts
    local_hashes = {r["image_sha256"] for r in records(tmp_path / "loc") if r["consent_scope"] == "local_only"}
    assert local_hashes and not any((tmp_path / "pub" / "crops" / f"{h}.jpg").exists() for h in local_hashes)


def test_records_are_complete_verified_and_pseudonymous(world: dict[str, Any], tmp_path: Path) -> None:
    a = add_correction(world, "public", "teh cat", "the cat")
    add_correction(world, "public", "dog", "dog [?]")
    add_correction(world, "local", "bird", "bird.")
    res = run(world, tmp_path / "x", "local")
    recs = {r["id"]: r for r in records(tmp_path / "x")}
    r = recs[str(a)]
    wanted = {"image", "image_sha256", "ocr_text", "corrected_text", "edit_ops", "crop_bbox", "crop_polygon", "page_image_sha256"}
    wanted |= {"preprocessing_version", "ocr", "subject", "consent_scope", "submission_ref", "examiner_ref", "created_at"}
    assert wanted <= set(r)
    assert r["ocr"]["provider"] == "paddle_v6" and r["subject"] == "EVS" and r["preprocessing_version"] == "none"
    crop = (tmp_path / "x" / r["image"]).read_bytes()
    assert hashlib.sha256(crop).hexdigest() == r["image_sha256"] and r["image_bytes"] == len(crop)
    jsonl = (tmp_path / "x" / "corrections.jsonl").read_bytes()
    assert res.manifest["corrections_jsonl_sha256"] == hashlib.sha256(jsonl).hexdigest()
    # no student reference, email, or raw booklet / examiner id anywhere in the export
    blob = "".join(p.read_text(errors="ignore") for p in (tmp_path / "x").rglob("*.json*"))
    secrets = [
        "ROLL-",
        "college-one.in",
        *map(str, (world["local"]["sub"], world["public"]["sub"])),
        str(world["users"]["examiner"]),
    ]
    for secret in secrets:
        assert secret not in blob, secret
    public_refs = {x["submission_ref"] for x in recs.values() if x["consent_scope"] == "public_release"}
    local_refs = {x["submission_ref"] for x in recs.values() if x["consent_scope"] == "local_only"}
    assert len(public_refs) == 1 and public_refs.isdisjoint(local_refs)  # a train/test split can keep a booklet together


def test_only_the_current_correction_of_a_line_unless_history_is_asked_for(world: dict[str, Any], tmp_path: Path) -> None:
    first = add_correction(world, "public", "wrod", "word")
    second = add_correction(world, "public", "wrod", "word.", supersedes=first)
    run(world, tmp_path / "h", "public", exam_id=world["exam"])
    heads = {r["id"] for r in records(tmp_path / "h")}
    assert str(second) in heads and str(first) not in heads
    full = run(world, tmp_path / "full", "public", exam_id=world["exam"], include_history=True)
    assert full.manifest["history_included"] is True
    by_id = {r["id"]: r for r in records(tmp_path / "full")}
    assert {str(first), str(second)} <= set(by_id) and by_id[str(second)]["supersedes_id"] == str(first)


def test_a_row_that_cannot_be_verified_is_reported_and_never_silently_dropped(world: dict[str, Any], tmp_path: Path) -> None:
    good = add_correction(world, "public", "good", "goood")
    missing = add_correction(world, "public", "gone", "gone!")
    tampered = add_correction(world, "public", "bent", "bent!")
    with world["sessions"]() as s:
        keys = {i: s.get(LineCorrection, i).crop_object_key for i in (missing, tampered)}  # type: ignore[union-attr]
    client = world["store"]._internal  # noqa: SLF001 - the test reaches under the module to damage stored objects
    client.remove_object(bucket_name=BUCKET, object_name=keys[missing])
    client.put_object(bucket_name=BUCKET, object_name=keys[tampered], data=io.BytesIO(b"other"), length=5)
    # a row whose edit operations do not reproduce its correction (inserted directly; the table is append-only)
    data = b"\xff\xd8\xff-ops-" + uuid.uuid4().bytes
    crop_key = world["store"].put(ObjectKind.LINE_CROP, io.BytesIO(data), len(data), "image/jpeg")
    bad = correction_row(world, "public", "ops", "opsy", crop_key, hashlib.sha256(data).hexdigest())
    bad.edit_ops = [{"op": "insert", "at": 0, "old": "", "new": "ZZZ"}]
    with world["sessions"]() as s, s.begin():
        s.add(bad)

    res = run(world, tmp_path / "v", "public", exam_id=world["exam"])
    reasons = {sk["reason"] for sk in res.skipped}
    assert not res.ok
    assert {"crop_missing", "crop_hash_mismatch", "edit_ops_do_not_reproduce_the_correction"} <= reasons
    assert str(good) in {r["id"] for r in records(tmp_path / "v")}  # the verifiable rows are still exported
    assert json.loads((tmp_path / "v" / "manifest.json").read_text())["skipped"] == res.skipped


def test_only_an_active_administrator_of_the_same_organisation_can_export(world: dict[str, Any], tmp_path: Path) -> None:
    add_correction(world, "public", "a", "b")
    for who in ("examiner@college-one.in", "gone@college-one.in", "nobody@college-one.in"):
        with pytest.raises(ExportRefusedError, match="administrator"):
            run(world, tmp_path / "no", "public", admin=who)
    assert not (tmp_path / "no").exists()  # refused before anything was written
    assert (
        run(world, tmp_path / "other-org", "local", admin="admin2@college-one.in").rows == 0
    )  # another organisation sees nothing
    with pytest.raises(ExportRefusedError, match="scope"):
        run(world, tmp_path / "bad", "everything")


def test_destination_rules_protect_the_public_repository(world: dict[str, Any], tmp_path: Path) -> None:
    add_correction(world, "public", "p", "q")
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "file").write_text("x")
    with pytest.raises(ExportRefusedError, match="never overwrites"):
        run(world, busy, "public")
    probe = ROOT / "zz_export_probe"  # inside the repo and NOT git-ignored
    ignored = ROOT / "datasets" / "zz_export_probe"  # inside the repo and git-ignored
    try:
        with pytest.raises(ExportRefusedError, match="PUBLIC"):
            run(world, probe, "public")
        assert not probe.exists()
        assert run(world, ignored, "public").rows >= 1 and (ignored / "manifest.json").exists()
        assert check_destination(tmp_path / "elsewhere") == (tmp_path / "elsewhere").resolve()  # outside any repo: fine
    finally:
        shutil.rmtree(probe, ignore_errors=True)
        shutil.rmtree(ignored, ignore_errors=True)
        if (ROOT / "datasets").exists() and not any((ROOT / "datasets").iterdir()):
            (ROOT / "datasets").rmdir()


def test_the_export_is_audited(world: dict[str, Any], tmp_path: Path) -> None:
    add_correction(world, "public", "z", "zz")
    res = run(world, tmp_path / "aud", "public")
    with world["sessions"]() as s:
        top = s.scalars(select(AuditLog).where(AuditLog.action == "dataset.export").order_by(AuditLog.at.desc())).first()
    assert top is not None
    assert top.actor_id == world["users"]["admin"] and top.details["scope"] == "public" and top.details["rows"] == res.rows
    assert top.details["dir"] == "aud" and "zz" not in json.dumps(top.details)  # no student text in the audit


def fresh_exam(w: dict[str, Any]) -> dict[str, Any]:
    """A new exam with one public booklet, so a test sees only its own (verifiable) rows."""
    with w["sessions"]() as s, s.begin():
        exam = Exam(
            org_id=s.get(User, w["users"]["admin"]).org_id,
            name="Clean",
            subject="EVS",
            total_marks=Decimal(5),
            created_by=w["users"]["admin"],
        )  # type: ignore[union-attr]
        s.add(exam)
        s.flush()
        sub = Submission(
            exam_id=exam.id,
            student_ref="ROLL-clean",
            consent_scope=ConsentScope.PUBLIC_RELEASE,
            source_object_key="k",
            source_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            source_mime="application/pdf",
            source_size_bytes=1,
            source_filename="c.pdf",
            created_by=w["users"]["admin"],
        )
        s.add(sub)
        s.flush()
        pg = Page(submission_id=sub.id, page_no=1, object_key="p", sha256="a" * 64, width=100, height=100)
        s.add(pg)
        s.flush()
        return {**w, "exam": exam.id, "public": {"sub": sub.id, "page": pg.id}}


def test_cli_exit_codes(world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0 = complete, 2 = refused (nothing written), 3 = written but some rows could not be verified."""
    clean = fresh_exam(world)
    add_correction(clean, "public", "c", "cc")
    monkeypatch.setenv("GRADEMIND_ENV", "test")
    monkeypatch.setenv("GRADEMIND_DATABASE_URL", URL or "")
    monkeypatch.setenv("GRADEMIND_S3_ENDPOINT_URL", S3 or "")
    monkeypatch.setenv("GRADEMIND_S3_ACCESS_KEY", KEYS[0])
    monkeypatch.setenv("GRADEMIND_S3_SECRET_KEY", KEYS[1])
    monkeypatch.setenv("GRADEMIND_S3_BUCKET", BUCKET)
    get_settings.cache_clear()
    admin = "admin@college-one.in"

    def cli(*args: str) -> int:
        return main(["export-corrections", "--scope", "public", *args])

    try:
        assert cli("--as-admin", admin, "--exam", str(clean["exam"]), "--out", str(tmp_path / "ok")) == 0
        assert (tmp_path / "ok" / "manifest.json").exists() and len(records(tmp_path / "ok")) == 1
        assert cli("--as-admin", "examiner@college-one.in", "--out", str(tmp_path / "r")) == 2 and not (tmp_path / "r").exists()
        add_correction(world, "public", "will", "break")  # make the original exam unverifiable
        with world["sessions"]() as s:
            victim = s.scalars(select(LineCorrection).where(LineCorrection.ocr_text == "will")).one()
            world["store"]._internal.remove_object(bucket_name=BUCKET, object_name=victim.crop_object_key)  # noqa: SLF001
        assert cli("--as-admin", admin, "--exam", str(world["exam"]), "--out", str(tmp_path / "partial")) == 3
        assert (tmp_path / "partial" / "manifest.json").exists()  # still written: the skipped rows are listed in it
    finally:
        get_settings.cache_clear()
