"""Operator CLI: bootstrap admin is idempotent, requires a strong password from the environment, and is audited."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from grademind_core.cli import main
from grademind_core.config import get_settings
from grademind_core.db.models import AuditLog, Role, User
from grademind_core.security import verify_password

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GRADEMIND_TEST_DATABASE_URL not set")
CORE = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    e = {**os.environ, "GRADEMIND_DATABASE_URL": URL or ""}
    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(CORE / "alembic.ini"), *cmd], env=e, check=True, capture_output=True
        )
    monkeypatch.setenv("GRADEMIND_ENV", "test")
    monkeypatch.setenv("GRADEMIND_DATABASE_URL", URL or "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_create_admin(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADEMIND_ADMIN_PASSWORD", "short")
    assert main(["create-admin", "--org", "College", "--email", "Admin@College.edu"]) == 2
    monkeypatch.setenv("GRADEMIND_ADMIN_PASSWORD", "a-long-enough-password")
    assert main(["create-admin", "--org", "College", "--email", "Admin@College.edu"]) == 0
    assert main(["create-admin", "--org", "College", "--email", "admin@college.edu"]) == 0  # idempotent
    engine = create_engine(URL or "")
    with Session(engine) as s:
        users = s.scalars(select(User)).all()
        assert len(users) == 1 and users[0].email == "admin@college.edu" and users[0].role == Role.ADMIN
        assert verify_password(users[0].password_hash, "a-long-enough-password")
        assert s.scalars(select(AuditLog.action)).all() == ["user.bootstrap_admin"]
    engine.dispose()


def test_create_user(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADEMIND_ADMIN_PASSWORD", "a-long-enough-password")
    assert main(["create-admin", "--org", "College", "--email", "admin@college.edu"]) == 0
    monkeypatch.setenv("GRADEMIND_USER_PASSWORD", "short")
    assert main(["create-user", "--org", "College", "--email", "ex@college.edu", "--role", "examiner"]) == 2
    monkeypatch.setenv("GRADEMIND_USER_PASSWORD", "examiner-password-123")
    assert main(["create-user", "--org", "Nowhere", "--email", "ex@college.edu", "--role", "examiner"]) == 2  # org must exist
    assert main(["create-user", "--org", "College", "--email", "Ex@College.edu", "--role", "examiner", "--name", "Ex"]) == 0
    assert main(["create-user", "--org", "College", "--email", "ex@college.edu", "--role", "examiner"]) == 0  # idempotent
    engine = create_engine(URL or "")
    with Session(engine) as s:
        admin = s.scalar(select(User).where(User.email == "admin@college.edu"))
        ex = s.scalar(select(User).where(User.email == "ex@college.edu"))
        assert admin is not None and ex is not None and ex.role == Role.EXAMINER and ex.org_id == admin.org_id
        assert verify_password(ex.password_hash, "examiner-password-123")
        assert s.scalars(select(AuditLog.action).where(AuditLog.action == "user.cli_create")).all() == ["user.cli_create"]
    engine.dispose()
