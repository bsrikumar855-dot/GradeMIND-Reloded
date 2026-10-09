"""Submission upload + storage through the API (spec §16): sniffing, size limits, dedup, RBAC, no storage paths, signed URLs.
Needs real Postgres and real MinIO."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import MAX_UPLOAD, login, needs_db, needs_s3
from sqlalchemy import select

from grademind_core.db.models import AuditLog, Submission

pytestmark = [needs_db, needs_s3]

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(scope="module")
def exam(world: dict[str, Any]) -> str:
    r = world["client"].post(
        "/api/exams", json={"name": "CIA 1", "subject": "EVS", "total_marks": "50"}, headers=login(world, "teacher")
    )
    assert r.status_code == 201, r.text
    eid: str = r.json()["id"]
    r = world["client"].post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    assert r.status_code == 204
    return eid


def upload(w: dict[str, Any], exam_id: str, data: bytes, name: str, who: str = "teacher", ref: str = "S-001") -> Any:
    return w["client"].post(
        f"/api/exams/{exam_id}/submissions",
        files={"file": (name, data, "application/octet-stream")},
        data={"student_ref": ref},
        headers=login(w, who),
    )


def test_upload_stores_by_uuid_key_and_never_returns_it(world: dict[str, Any], exam: str) -> None:
    body = PDF + b"%unique-1"
    r = upload(world, exam, body, "../../Ravi Kumar answer script.pdf")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["mime"] == "application/pdf" and out["size_bytes"] == len(body)
    assert out["sha256"] == hashlib.sha256(body).hexdigest() and out["consent_scope"] == "local_only"
    assert out["filename"] == "Ravi Kumar answer script.pdf"  # sanitised: no path components
    assert "submission-source" not in r.text and "object_key" not in r.text
    with world["sessions"]() as s:
        sub = s.get(Submission, out["id"])
        assert sub is not None and sub.source_object_key.startswith("submission-source/")
        assert world["store"].get_bytes(sub.source_object_key) == body
        audit = s.scalars(select(AuditLog).where(AuditLog.entity_id == out["id"])).one()
        assert audit.action == "submission.create" and audit.request_id == r.headers["x-request-id"]
        assert "Ravi" not in json.dumps(audit.details)  # filenames may carry student names: never audited


def test_signed_url_serves_the_file_and_expires(world: dict[str, Any], exam: str) -> None:
    body = PNG + b"unique-2"
    sid = upload(world, exam, body, "p1.png").json()["id"]
    d = world["client"].get(f"/api/submissions/{sid}", headers=login(world, "exA"))
    assert d.status_code == 200, d.text
    url = d.json()["source_url"]
    assert d.json()["source_url_expires_in"] == world["settings"].signed_url_ttl_seconds
    assert parse_qs(urlsplit(url).query)["X-Amz-Expires"] == [str(world["settings"].signed_url_ttl_seconds)]
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - test-controlled http URL
        assert resp.read() == body


@pytest.mark.parametrize(
    ("data", "name", "status", "code"),
    [
        (b"MZ\x90\x00exe", "answers.pdf", 422, "unsupported_type"),
        (b"<html><script>x</script>", "answers.pdf", 422, "unsupported_type"),
        (PNG + b"mismatch", "answers.pdf", 422, "type_mismatch"),
        (b"", "answers.pdf", 422, "empty"),
    ],
)
def test_rejected_uploads_store_nothing(world: dict[str, Any], exam: str, data: bytes, name: str, status: int, code: str) -> None:
    with world["sessions"]() as s:
        before = len(s.scalars(select(Submission)).all())
    r = upload(world, exam, data, name)
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert r.json()["error"]["request_id"] == r.headers["x-request-id"]
    with world["sessions"]() as s:
        assert len(s.scalars(select(Submission)).all()) == before


def test_duplicate_file_is_detected(world: dict[str, Any], exam: str) -> None:
    body = PDF + b"%unique-dup"
    assert upload(world, exam, body, "a.pdf").status_code == 201
    r = upload(world, exam, body, "renamed.pdf", ref="S-002")
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_upload"


def test_oversized_upload_is_rejected_with_declared_length(world: dict[str, Any], exam: str) -> None:
    r = upload(world, exam, PDF + b"0" * (MAX_UPLOAD + 128 * 1024), "big.pdf")
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"


def test_oversized_chunked_upload_is_cut_off(world: dict[str, Any], exam: str) -> None:
    """No Content-Length (chunked): the body is counted as it arrives and the request is cut off at the limit."""
    boundary = "gmboundary"

    def body() -> Iterator[bytes]:
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="student_ref"\r\n\r\nS-9\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="x.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + PDF
        for _ in range(64):  # 64 x 64 KiB = 4 MiB, far beyond the 256 KiB test limit
            yield b"0" * 64 * 1024
        yield f"\r\n--{boundary}--\r\n".encode()

    h = {**login(world, "teacher"), "content-type": f"multipart/form-data; boundary={boundary}"}
    r = world["client"].post(f"/api/exams/{exam}/submissions", content=body(), headers=h)
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    assert "content-length" not in {k.lower() for k in r.request.headers}
    # (TestClient buffers the body client-side, so where the cut-off happens is proven in test_body_limit.py)


def test_upload_rbac_and_visibility(world: dict[str, Any], exam: str) -> None:
    c = world["client"]
    assert upload(world, exam, PDF + b"%rbac", "x.pdf", who="exA").status_code == 403  # examiners grade; they do not upload
    sid = upload(world, exam, PDF + b"%rbac-2", "x.pdf").json()["id"]
    assert c.get(f"/api/exams/{exam}/submissions", headers=login(world, "exA")).status_code == 200  # assigned
    for who in ("exB", "admin2", "ex2"):  # not assigned / other org: existence is not revealed
        assert c.get(f"/api/exams/{exam}/submissions", headers=login(world, who)).status_code == 404
        assert c.get(f"/api/submissions/{sid}", headers=login(world, who)).status_code == 404
    assert upload(world, exam, PDF + b"%rbac-3", "x.pdf", who="admin2").status_code == 404
    assert c.get(f"/api/submissions/{sid}").status_code == 401


@pytest.mark.parametrize("ref", ["Ravi Kumar", "", "a/b", "x" * 65, "../etc"])
def test_student_ref_must_be_a_pseudonymous_identifier(world: dict[str, Any], exam: str, ref: str) -> None:
    r = upload(world, exam, PDF + ref.encode(), "x.pdf", ref=ref)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_listing_is_paginated(world: dict[str, Any], exam: str) -> None:
    c = world["client"]
    h = login(world, "teacher")
    everything = c.get(f"/api/exams/{exam}/submissions?limit=100", headers=h).json()
    page1 = c.get(f"/api/exams/{exam}/submissions?limit=2&offset=0", headers=h).json()
    page2 = c.get(f"/api/exams/{exam}/submissions?limit=2&offset=2", headers=h).json()
    assert [x["id"] for x in page1 + page2] == [x["id"] for x in everything[:4]]
    assert c.get(f"/api/exams/{exam}/submissions?limit=1000", headers=h).status_code == 422


def test_storage_health(world: dict[str, Any]) -> None:
    assert world["client"].get("/health/storage").json() == {"status": "ok"}
