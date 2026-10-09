"""Shared API test world: real Postgres (migrated by Alembic) and, when configured, real MinIO.

DB-backed tests skip unless GRADEMIND_TEST_DATABASE_URL is set; storage-backed tests also need GRADEMIND_TEST_S3_ENDPOINT.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from grademind_api.app import create_app
from grademind_core.config import Env, Settings
from grademind_core.db.models import Exam, Organization, Role, User
from grademind_core.security import hash_password
from grademind_core.storage import ObjectStore

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
S3 = os.environ.get("GRADEMIND_TEST_S3_ENDPOINT")
needs_db = pytest.mark.skipif(not URL, reason="GRADEMIND_TEST_DATABASE_URL not set (API tests need real Postgres)")
needs_s3 = pytest.mark.skipif(not S3, reason="GRADEMIND_TEST_S3_ENDPOINT not set (upload tests need MinIO)")
CORE = Path(__file__).resolve().parents[3] / "packages" / "core"
PW = "correct horse battery staple"
MAX_UPLOAD = 256 * 1024


def alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config(str(CORE / "alembic.ini"))).get_current_head()
    assert head is not None
    return head


@pytest.fixture(scope="module")
def world() -> Iterator[dict[str, Any]]:
    env = {**os.environ, "GRADEMIND_DATABASE_URL": URL or ""}
    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(CORE / "alembic.ini"), *cmd], env=env, check=True, capture_output=True
        )
    engine = create_engine(URL or "")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    w: dict[str, Any] = {"sessions": sessions}
    with sessions() as s, s.begin():
        o1, o2 = Organization(name="Org1"), Organization(name="Org2")
        s.add_all([o1, o2])
        s.flush()
        for key, org, role in [
            ("admin", o1, Role.ADMIN),
            ("teacher", o1, Role.TEACHER),
            ("exA", o1, Role.EXAMINER),
            ("exB", o1, Role.EXAMINER),
            ("admin2", o2, Role.ADMIN),
            ("ex2", o2, Role.EXAMINER),
        ]:
            u = User(org_id=org.id, email=f"{key}@college-one.in", display_name=key, password_hash=hash_password(PW), role=role)
            s.add(u)
            s.flush()
            w[key] = u.id
        e2 = Exam(org_id=o2.id, name="Other org exam", subject="X", total_marks=Decimal(10), created_by=w["admin2"])
        s.add(e2)
        s.flush()
        w["exam_org2"] = e2.id
    settings = Settings(
        env=Env.TEST,
        database_url=URL or "",
        jwt_secret="t" * 40,
        redis_url="redis://127.0.0.1:1/0",
        ocr_service_url="http://127.0.0.1:1",
        s3_endpoint_url=S3 or "http://127.0.0.1:1",
        s3_access_key=os.environ.get("GRADEMIND_TEST_S3_ACCESS_KEY", ""),
        s3_secret_key=os.environ.get("GRADEMIND_TEST_S3_SECRET_KEY", ""),
        s3_bucket="gm-test-api",
        max_upload_bytes=MAX_UPLOAD,
    )
    store = ObjectStore(settings)
    if S3:
        store.ensure_bucket()
    w["settings"], w["store"] = settings, store
    with TestClient(create_app(settings, sessions, store)) as client:
        w["client"] = client
        yield w
    engine.dispose()


def login(w: dict[str, Any], who: str) -> dict[str, str]:
    r = w["client"].post("/api/auth/login", json={"email": f"{who}@college-one.in", "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
