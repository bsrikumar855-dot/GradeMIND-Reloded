"""Backup and restore (4.6): the object archive round trip, damage and truncation detection, and the restore verification.
Real Postgres + MinIO. The pg_dump / pg_restore half is exercised by the CI restore drill (scripts/backup.sh, restore.sh)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from grademind_core.backup import (
    FORMAT,
    MANIFEST,
    PAX_SHA,
    read_archive,
    table_counts,
    verify_restore,
    write_archive,
)
from grademind_core.config import Env, Settings
from grademind_core.db.models import ConsentScope, Exam, Organization, Page, Role, Submission, User
from grademind_core.storage import ObjectKind, ObjectStore

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
S3 = os.environ.get("GRADEMIND_TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(not (URL and S3), reason="needs GRADEMIND_TEST_DATABASE_URL and GRADEMIND_TEST_S3_ENDPOINT")
CORE = Path(__file__).resolve().parents[1]
KEYS = os.environ.get("GRADEMIND_TEST_S3_ACCESS_KEY", ""), os.environ.get("GRADEMIND_TEST_S3_SECRET_KEY", "")


def store_for(bucket: str) -> ObjectStore:
    s = ObjectStore(
        Settings(env=Env.TEST, s3_endpoint_url=S3 or "", s3_access_key=KEYS[0], s3_secret_key=KEYS[1], s3_bucket=bucket)
    )
    s.ensure_bucket()
    return s


def fresh_bucket(tag: str) -> ObjectStore:
    """A bucket nothing has been written to (a restore target must start empty for the comparisons to mean anything)."""
    name = f"gm-test-bk-{tag}-{uuid.uuid4().hex[:8]}"
    return store_for(name)


def put(store: ObjectStore, kind: ObjectKind, data: bytes, ctype: str = "application/octet-stream") -> str:
    return store.put(kind, io.BytesIO(data), len(data), ctype)


def archive_of(store: ObjectStore) -> bytes:
    out = io.BytesIO()
    write_archive(store, out)
    return out.getvalue()


def tar_members(blob: bytes) -> list[tuple[tarfile.TarInfo, bytes]]:
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        return [(m, (f.read() if (f := tar.extractfile(m)) else b"")) for m in tar]


def keys_and_bytes(store: ObjectStore) -> dict[str, bytes]:
    return {k: store.get_bytes(k) for k in store.list_keys()}


def test_objects_round_trip_with_their_content_types() -> None:
    src, dst = fresh_bucket("src"), fresh_bucket("dst")
    page = put(src, ObjectKind.PAGE_IMAGE, b"\xff\xd8\xff" + os.urandom(3000), "image/jpeg")
    pdf = put(src, ObjectKind.SUBMISSION_SOURCE, b"%PDF-1.4 " + os.urandom(500), "application/pdf")
    empty = put(src, ObjectKind.LINE_CROP, b"", "image/jpeg")  # a zero-byte object is still an object
    blob = archive_of(src)
    rep = read_archive(dst, io.BytesIO(blob))
    assert rep.ok and rep.restored == 3
    assert keys_and_bytes(dst) == keys_and_bytes(src)
    assert {k: dst.content_type_of(k) for k in (page, pdf, empty)} == {
        page: "image/jpeg",
        pdf: "application/pdf",
        empty: "image/jpeg",
    }
    manifest = json.loads(dict((m.name, d) for m, d in tar_members(blob))[MANIFEST])
    assert (
        manifest["format"] == FORMAT and manifest["count"] == 3 and {e["key"] for e in manifest["objects"]} == {page, pdf, empty}
    )


def test_an_empty_bucket_makes_a_valid_empty_archive() -> None:
    rep = read_archive(fresh_bucket("e2"), io.BytesIO(archive_of(fresh_bucket("e1"))))
    assert rep.ok and rep.restored == 0


def test_a_damaged_member_is_reported_and_not_stored() -> None:
    src, dst = fresh_bucket("src"), fresh_bucket("dst")
    good = put(src, ObjectKind.PAGE_IMAGE, b"good-object-" + os.urandom(64))
    bad = put(src, ObjectKind.PAGE_IMAGE, b"A" * 2048)
    blob = bytearray(archive_of(src))
    at = bytes(blob).index(b"A" * 100) + 10  # inside the payload of `bad`
    blob[at] ^= 0xFF
    rep = read_archive(dst, io.BytesIO(bytes(blob)))
    assert not rep.ok and any(bad in p and "SHA-256" in p for p in rep.problems)
    assert dst.list_keys() == [good]  # the damaged object was NOT stored; the good one was


def test_a_truncated_archive_is_never_reported_as_complete() -> None:
    src = fresh_bucket("src")
    for _ in range(3):
        put(src, ObjectKind.PAGE_IMAGE, os.urandom(4000))
    blob = archive_of(src)
    # (a tar ends in zero padding, so cut where it matters: through the middle of an object, and just before the closing manifest)
    for cut in (blob.index(b"objects/") + 2000, blob.index(MANIFEST.encode()) - 10):
        rep = read_archive(fresh_bucket("dst"), io.BytesIO(blob[:cut]))
        assert not rep.ok and any("truncated" in p or "no manifest" in p for p in rep.problems), rep.problems
    rep = read_archive(fresh_bucket("dst"), io.BytesIO(b"this is not an archive at all"))
    assert not rep.ok


def test_a_member_missing_from_the_archive_is_found_through_the_manifest() -> None:
    src, dst = fresh_bucket("src"), fresh_bucket("dst")
    keep = put(src, ObjectKind.PAGE_IMAGE, b"keep" * 100)
    drop = put(src, ObjectKind.PAGE_IMAGE, b"drop" * 100)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for m, data in tar_members(archive_of(src)):
            if m.name == f"objects/{drop}":
                continue  # lose one member, keep the manifest that still lists it
            tar.addfile(m, io.BytesIO(data))
    rep = read_archive(dst, io.BytesIO(out.getvalue()))
    assert not rep.ok and any(drop in p and "missing from the archive" in p for p in rep.problems)
    assert dst.list_keys() == [keep]


def test_a_member_swapped_for_another_valid_object_does_not_match_the_manifest() -> None:
    src, dst = fresh_bucket("src"), fresh_bucket("dst")
    k = put(src, ObjectKind.PAGE_IMAGE, b"original" * 50)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for m, data in tar_members(archive_of(src)):
            if m.name == f"objects/{k}":
                forged = b"forged" * 50  # a self-consistent member: its own hash matches its bytes, but not the manifest
                info = tarfile.TarInfo(m.name)
                info.size = len(forged)
                info.pax_headers = {PAX_SHA: hashlib.sha256(forged).hexdigest()}
                tar.addfile(info, io.BytesIO(forged))
            else:
                tar.addfile(m, io.BytesIO(data))
    rep = read_archive(dst, io.BytesIO(out.getvalue()))
    assert not rep.ok and any(k in p and "differs from the manifest" in p for p in rep.problems)


def test_the_cli_round_trips_through_stdout_and_stdin(tmp_path: Path) -> None:
    src_name, dst = f"gm-test-bk-cli-{uuid.uuid4().hex[:8]}", f"gm-test-bk-cli-{uuid.uuid4().hex[:8]}"
    src = store_for(src_name)
    put(src, ObjectKind.PAGE_IMAGE, b"cli-" + os.urandom(100), "image/jpeg")
    env = {
        **os.environ,
        "GRADEMIND_ENV": "test",
        "GRADEMIND_S3_ENDPOINT_URL": S3 or "",
        "GRADEMIND_S3_ACCESS_KEY": KEYS[0],
        "GRADEMIND_S3_SECRET_KEY": KEYS[1],
        "GRADEMIND_S3_BUCKET": src_name,
    }
    cmd = [sys.executable, "-m", "grademind_core.cli"]
    out = subprocess.run([*cmd, "backup-objects"], env=env, capture_output=True, check=True)
    assert b"backed up 1 object" in out.stderr
    back = subprocess.run([*cmd, "restore-objects", "--bucket", dst], env=env, input=out.stdout, capture_output=True, check=True)
    assert b"restored 1 object" in back.stderr
    assert keys_and_bytes(store_for(dst)) == keys_and_bytes(src)
    broken = bytearray(out.stdout)
    broken[out.stdout.index(b"cli-") + 8] ^= 0xFF  # a byte inside the object's own bytes
    bad = subprocess.run([*cmd, "restore-objects", "--bucket", f"{dst}x"], env=env, input=bytes(broken), capture_output=True)
    assert bad.returncode == 1 and b"RESTORE PROBLEM" in bad.stderr


# ------------------------------------------------------------------------------------------------ verify_restore


@pytest.fixture(scope="module")
def db() -> Iterator[sessionmaker[Any]]:
    env = {**os.environ, "GRADEMIND_DATABASE_URL": URL or ""}
    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(CORE / "alembic.ini"), *cmd], env=env, check=True, capture_output=True
        )
    engine = create_engine(URL or "")
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def head_revision(sessions: sessionmaker[Any]) -> str:
    with sessions() as s:
        return str(s.scalar(text("SELECT version_num FROM alembic_version")))


def seed(sessions: sessionmaker[Any], store: ObjectStore) -> dict[str, str]:
    """One booklet with a stored source and one page (image + thumbnail), their hashes recorded in the database."""
    src, img, thumb = b"%PDF-1.4 source " + os.urandom(64), b"\xff\xd8\xff page " + os.urandom(64), b"\xff\xd8\xff thumb"
    keys = {
        "source": put(store, ObjectKind.SUBMISSION_SOURCE, src, "application/pdf"),
        "image": put(store, ObjectKind.PAGE_IMAGE, img, "image/jpeg"),
        "thumb": put(store, ObjectKind.PAGE_THUMB, thumb, "image/jpeg"),
    }
    with sessions() as s, s.begin():
        org = Organization(name=f"Org-{uuid.uuid4().hex[:6]}")
        s.add(org)
        s.flush()
        u = User(
            org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@college-one.in", display_name="a", password_hash="x", role=Role.ADMIN
        )
        s.add(u)
        s.flush()
        exam = Exam(org_id=org.id, name="CIA", subject="EVS", total_marks=Decimal(10), created_by=u.id)
        s.add(exam)
        s.flush()
        sub = Submission(
            exam_id=exam.id,
            student_ref="BK-1",
            consent_scope=ConsentScope.LOCAL_ONLY,
            source_object_key=keys["source"],
            source_sha256=hashlib.sha256(src).hexdigest(),
            source_mime="application/pdf",
            source_size_bytes=len(src),
            source_filename="b.pdf",
            created_by=u.id,
        )
        s.add(sub)
        s.flush()
        s.add(
            Page(
                submission_id=sub.id,
                page_no=1,
                object_key=keys["image"],
                thumb_object_key=keys["thumb"],
                sha256=hashlib.sha256(img).hexdigest(),
                width=10,
                height=10,
            )
        )
    return keys


def test_verify_restore_passes_a_whole_restore_and_names_every_kind_of_damage(db: sessionmaker[Any]) -> None:
    store = fresh_bucket("v")
    keys = seed(db, store)
    rev = head_revision(db)
    with db() as s:
        before = table_counts(s)
        ok = verify_restore(s, store, rev, before)
    assert ok.ok and ok.objects_checked >= 2 and ok.counts["pages"] >= 1

    with db() as s:  # the schema is not at the revision this code expects
        assert any("revision" in p for p in verify_restore(s, store, "0000", before).problems)
        wrong = {**before, "pages": before["pages"] + 1}  # the rows differ from what the backup counted
        assert any("pages:" in p for p in verify_restore(s, store, rev, wrong).problems)

    # an object that was never restored, one that came back altered, and a missing thumbnail
    thumb_bytes = store.get_bytes(keys["thumb"])
    store._internal.remove_object(store._bucket, keys["thumb"])  # noqa: SLF001 - simulate the loss
    store._internal.remove_object(store._bucket, keys["source"])  # noqa: SLF001
    store.put_at(keys["image"], b"altered", "image/jpeg")
    with db() as s:
        problems = verify_restore(s, store, rev, before).problems
    assert any("booklet" in p and "missing" in p for p in problems)
    assert any("thumbnail" in p and "missing" in p for p in problems)
    assert any("image" in p and "SHA-256" in p for p in problems)
    assert thumb_bytes  # (kept only so the lines above read as a story)
