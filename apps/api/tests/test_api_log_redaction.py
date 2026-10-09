"""D26.4 regression: no part of a malformed login request, and no token, ever reaches the logs (any logger)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from conftest import login, needs_db
from fastapi.testclient import TestClient

pytestmark = needs_db
PW = "Hunter2-Unique-Password-7781"


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"json": {"password": PW}}, 422),  # missing email
        ({"json": {"email": "not-an-email", "password": PW}}, 422),
        ({"json": {"email": "admin@college-one.in", "password": PW, "extra": PW}}, 401),  # wrong password
        ({"json": {"email": ["admin@college-one.in"], "password": [PW]}}, 422),  # wrong types
        (
            {
                "content": '{"email": "admin@college-one.in", "password": "' + PW + '"',
                "headers": {"content-type": "application/json"},
            },
            422,
        ),  # broken JSON
        ({"data": {"email": "admin@college-one.in", "password": PW}}, 422),  # form-encoded instead of JSON
        ({"content": PW * 200, "headers": {"content-type": "application/json"}}, 422),  # garbage
    ],
)
def test_malformed_login_never_appears_in_logs(
    world: dict[str, Any], caplog: pytest.LogCaptureFixture, kwargs: dict[str, Any], status: int
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = world["client"].post("/api/auth/login", **kwargs)
    assert r.status_code == status
    assert PW not in caplog.text
    assert PW not in r.text  # nor echoed back to the client


def test_tokens_never_appear_in_logs(world: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    h = login(world, "admin")
    token = h["Authorization"].split()[1]
    app = world["client"].app

    def boom() -> None:
        raise RuntimeError(f"downstream rejected Authorization: Bearer {token}; password={PW}")

    if not any(getattr(r, "path", "") == "/__test_boom" for r in app.routes):
        app.add_api_route("/__test_boom", boom)
    client = TestClient(app, raise_server_exceptions=False)  # see the real 500 path, not the test client's re-raise
    with caplog.at_level(logging.DEBUG):
        client.get("/api/me", headers=h)
        client.get("/api/me", headers={"Authorization": f"Bearer {token}x"})  # tampered -> 401
        r = client.get("/__test_boom", headers=h)
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert token not in caplog.text and PW not in caplog.text
    assert "RuntimeError" in caplog.text  # the failure is still logged, without the secrets
    ours = [rec.getMessage() for rec in caplog.records if rec.name == "grademind.api"]
    assert ours and all(isinstance(json.loads(m), dict) for m in ours)  # redaction keeps structured lines valid JSON
