"""API tests: auth + RBAC denials (spec §16, §20), error envelope, health. Real Postgres via GRADEMIND_TEST_DATABASE_URL."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
from conftest import alembic_head, login, needs_db
from sqlalchemy import select

from grademind_core.db.models import AuditLog

pytestmark = needs_db


def test_health_endpoints(world: dict[str, Any]) -> None:
    c = world["client"]
    assert c.get("/health").json() == {"status": "ok"}
    r = c.get("/health/db")
    assert r.status_code == 200 and r.json()["alembic_revision"] == alembic_head()
    m = c.get("/health/models").json()
    assert m["ai_suggestions_enabled"] is False and m["llm_kill_switch"] is True and m["ocr_providers_enabled"] == ["paddle_v6"]
    assert c.get("/health/redis").status_code == 503  # unreachable redis is reported, not hidden
    assert c.get("/health/ocr").status_code == 503


def test_login_failures_share_one_message_and_envelope(world: dict[str, Any]) -> None:
    c = world["client"]
    bad_pw = c.post("/api/auth/login", json={"email": "admin@college-one.in", "password": "nope"})
    no_user = c.post("/api/auth/login", json={"email": "ghost@college-one.in", "password": "nope"})
    for r in (bad_pw, no_user):
        assert r.status_code == 401
        err = r.json()["error"]
        assert err["code"] == "invalid_credentials" and err["request_id"] == r.headers["x-request-id"]
    assert bad_pw.json()["error"]["message"] == no_user.json()["error"]["message"]


def test_unauthenticated_and_bad_tokens(world: dict[str, Any]) -> None:
    c = world["client"]
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401
    assert c.get("/api/exams").status_code == 401


def test_rbac_create_and_examiner_scope(world: dict[str, Any]) -> None:
    c = world["client"]
    body = {"name": "CIA-1", "subject": "EVS", "total_marks": "50"}
    assert c.post("/api/exams", json=body, headers=login(world, "exA")).status_code == 403  # examiner cannot create
    r = c.post("/api/exams", json=body, headers=login(world, "teacher"))
    assert r.status_code == 201
    exam_id = r.json()["id"]
    ha, hb = login(world, "exA"), login(world, "exB")
    assert c.get("/api/exams", headers=ha).json() == []  # not assigned yet
    assert c.get(f"/api/exams/{exam_id}", headers=ha).status_code == 404
    # only admins assign
    assert (
        c.post(
            f"/api/exams/{exam_id}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "teacher")
        ).status_code
        == 403
    )
    assert (
        c.post(
            f"/api/exams/{exam_id}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin")
        ).status_code
        == 204
    )
    assert [e["id"] for e in c.get("/api/exams", headers=ha).json()] == [exam_id]
    assert c.get(f"/api/exams/{exam_id}", headers=ha).status_code == 200
    assert c.get(f"/api/exams/{exam_id}", headers=hb).status_code == 404  # exB not assigned: existence not revealed
    assert c.get("/api/exams", headers=hb).json() == []
    # teacher (non-examiner) cannot be assigned
    assert (
        c.post(
            f"/api/exams/{exam_id}/assignments", json={"user_id": str(world["teacher"])}, headers=login(world, "admin")
        ).status_code
        == 422
    )


def test_cross_org_isolation(world: dict[str, Any]) -> None:
    c = world["client"]
    h = login(world, "admin")
    assert c.get(f"/api/exams/{world['exam_org2']}", headers=h).status_code == 404
    assert all(e["id"] != str(world["exam_org2"]) for e in c.get("/api/exams", headers=h).json())
    r = c.post(f"/api/exams/{world['exam_org2']}/assignments", json={"user_id": str(world["ex2"])}, headers=h)
    assert r.status_code == 404
    ex = c.post("/api/exams", json={"name": "E", "subject": "S", "total_marks": "5"}, headers=h).json()["id"]
    r = c.post(f"/api/exams/{ex}/assignments", json={"user_id": str(world["ex2"])}, headers=h)  # examiner from another org
    assert r.status_code == 422


def test_actions_are_audited(world: dict[str, Any]) -> None:
    with world["sessions"]() as s:
        actions = set(s.scalars(select(AuditLog.action)))
    assert {"auth.login", "exam.create", "exam.assign"} <= actions


def test_validation_error_is_human_and_has_request_id(world: dict[str, Any]) -> None:
    r = world["client"].post("/api/exams", json={"name": "", "subject": "S", "total_marks": "-1"}, headers=login(world, "admin"))
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "Some fields are missing or invalid." and r.json()["error"]["request_id"]
    assert uuid.UUID(hex=r.json()["error"]["request_id"])


def test_validation_logs_never_contain_submitted_values(world: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    """Regression: pydantic puts the offending input into e.errors(); for a login body that includes the password."""
    with caplog.at_level(logging.INFO, logger="grademind.api"):
        r = world["client"].post("/api/auth/login", json={"password": "hunter2-very-secret"})
    assert r.status_code == 422
    assert "validation" in caplog.text and "hunter2-very-secret" not in caplog.text
