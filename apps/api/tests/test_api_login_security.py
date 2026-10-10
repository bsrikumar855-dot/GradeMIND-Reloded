"""4.6 security pass, API side: sign-in throttling, a miss costing the same as a hit, security headers, no API docs in production.
Real Postgres. Each throttling test uses its own account name so the tests do not see each other's failures."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import PW, needs_db
from fastapi.testclient import TestClient
from sqlalchemy import select

from grademind_api.app import create_app
from grademind_core.config import Env, Settings
from grademind_core.db.models import AuditLog, LoginAttempt
from grademind_core.login_throttle import Limits, record, retry_after

pytestmark = needs_db


def app_with(world: dict[str, Any], **overrides: Any) -> TestClient:
    settings: Settings = world["settings"].model_copy(update=overrides)
    return TestClient(create_app(settings, world["sessions"], world["store"], world["queue"]))


def attempt(c: TestClient, email: str, password: str = "wrong", ip: str | None = None) -> Any:  # noqa: S107
    headers = {"x-forwarded-for": ip} if ip else {}
    return c.post("/api/auth/login", json={"email": email, "password": password}, headers=headers)


def attempts(world: dict[str, Any], email: str) -> int:
    with world["sessions"]() as s:
        return len(list(s.scalars(select(LoginAttempt).where(LoginAttempt.email == email))))


def test_the_fifth_failure_is_the_last_the_sixth_is_refused_even_with_the_right_password(world: dict[str, Any]) -> None:
    c = app_with(world, login_max_failures_per_account_and_ip=5)
    email = "admin@college-one.in"
    for _ in range(5):
        assert attempt(c, email).status_code == 401
    r = attempt(c, email)
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_attempts"
    assert 1 <= int(r.headers["retry-after"]) <= 900
    assert attempt(c, email, PW).status_code == 429  # the correct password does not get through while the limit is in force
    # a different account from the same address is a different bucket
    assert attempt(c, "teacher@college-one.in", PW).status_code == 200


def test_one_person_cannot_lock_the_real_user_out_from_their_own_address(world: dict[str, Any]) -> None:
    """With the forwarded address trusted, a stranger's failures lock only (account, stranger's address)."""
    c = app_with(world, trust_forwarded_for=True, login_max_failures_per_account_and_ip=3, login_max_failures_per_account=50)
    email = "exa@college-one.in"
    for _ in range(3):
        assert attempt(c, email, ip="203.0.113.50").status_code == 401
    assert attempt(c, email, ip="203.0.113.50").status_code == 429  # the stranger is stopped
    ok = attempt(c, "exA@college-one.in", PW, ip="198.51.100.7")
    assert ok.status_code == 200  # the real user, from their own address, is not


def test_one_address_trying_many_accounts_is_stopped_but_an_unknown_address_is_not_lumped_together(world: dict[str, Any]) -> None:
    c = app_with(world, trust_forwarded_for=True, login_max_failures_per_ip=4, login_max_failures_per_account_and_ip=99)
    for i in range(4):
        assert attempt(c, f"nobody{i}@college-one.in", ip="203.0.113.60").status_code == 401
    assert attempt(c, "nobody9@college-one.in", ip="203.0.113.60").status_code == 429
    assert (
        attempt(c, "teacher@college-one.in", PW, ip="203.0.113.60").status_code == 429
    )  # even a real account, from that address
    assert attempt(c, "teacher@college-one.in", PW, ip="203.0.113.61").status_code == 200


def test_an_address_that_cannot_be_told_is_not_a_bucket_of_its_own(world: dict[str, Any]) -> None:
    """An unknown source would share one per-address limit among all users behind it: that limit is skipped."""
    lim = Limits(timedelta(seconds=900), 99, 3, 99)
    with world["sessions"]() as s, s.begin():
        for i in range(10):  # ten failures for ten different accounts, all from "unknown"
            s.add(LoginAttempt(email=f"u{i}-{uuid.uuid4().hex}@college-one.in", ip="unknown", success=False))
    with world["sessions"]() as s:
        assert retry_after(s, "someone@college-one.in", "unknown", lim) is None
        assert (
            retry_after(s, "someone@college-one.in", "203.0.113.99", lim) is None
        )  # and a known address has its own clean bucket


def test_the_forwarded_address_is_ignored_unless_it_is_trusted(world: dict[str, Any]) -> None:
    """Without a trusted proxy a caller could invent a new address for every attempt and never reach the limit."""
    c = app_with(world, trust_forwarded_for=False, login_max_failures_per_account_and_ip=3)
    email = "exb@college-one.in"
    codes = [attempt(c, email, ip=f"203.0.113.{i}").status_code for i in range(5)]
    assert codes == [401, 401, 401, 429, 429]


def test_a_spread_out_attack_on_one_account_is_stopped_by_the_account_limit(world: dict[str, Any]) -> None:
    c = app_with(world, trust_forwarded_for=True, login_max_failures_per_account=4, login_max_failures_per_account_and_ip=99)
    email = "ex2@college-one.in"
    for i in range(4):
        assert attempt(c, email, ip=f"203.0.113.{100 + i}").status_code == 401
    assert attempt(c, email, PW, ip="203.0.113.200").status_code == 429


def test_the_limit_lifts_when_old_failures_leave_the_window_and_retry_after_says_when(world: dict[str, Any]) -> None:
    email = f"window-{uuid.uuid4().hex}@college-one.in"
    lim = Limits(timedelta(seconds=900), 3, 30, 50)
    now = datetime.now(UTC)
    with world["sessions"]() as s, s.begin():
        for age in (850, 600, 300):  # three failures; the oldest leaves the window in 50 s
            s.add(LoginAttempt(email=email, ip="198.51.100.1", success=False, at=now - timedelta(seconds=age)))
    with world["sessions"]() as s:
        wait = retry_after(s, email, "198.51.100.1", lim, now)
    assert wait is not None and wait[1] == "account_and_ip" and 49 <= wait[0] <= 52
    with world["sessions"]() as s:
        assert retry_after(s, email, "198.51.100.1", lim, now + timedelta(seconds=60)) is None  # one has aged out: 2 < 3
        assert retry_after(s, email, "198.51.100.2", lim, now) is None  # another address is not affected


def test_successes_are_never_counted_and_old_rows_are_pruned(world: dict[str, Any]) -> None:
    email = f"ok-{uuid.uuid4().hex}@college-one.in"
    lim = Limits(timedelta(seconds=900), 2, 30, 50)
    with world["sessions"]() as s, s.begin():
        s.add(LoginAttempt(email=email, ip="198.51.100.3", success=False, at=datetime.now(UTC) - timedelta(days=2)))
        for _ in range(10):
            record(s, email, "198.51.100.3", True, lim)
    with world["sessions"]() as s:
        assert retry_after(s, email, "198.51.100.3", lim) is None  # ten successes: no limit
        old = [
            a
            for a in s.scalars(select(LoginAttempt).where(LoginAttempt.email == email))
            if a.at < datetime.now(UTC) - timedelta(days=1)
        ]
        assert old == []  # the two-day-old row was deleted by the next attempt


def test_reaching_a_limit_is_audited_once_without_the_address_of_a_person(world: dict[str, Any]) -> None:
    c = app_with(world, login_max_failures_per_account_and_ip=3)
    email = f"audit-{uuid.uuid4().hex}@college-one.in"
    for _ in range(8):
        attempt(c, email)
    with world["sessions"]() as s:
        rows = list(s.scalars(select(AuditLog).where(AuditLog.action == "auth.login_throttled").order_by(AuditLog.at)))
    mine = [r for r in rows if r.details.get("limit") == "account_and_ip"]
    assert len(mine) >= 1 and all(
        email not in str(r.details) for r in rows
    )  # the moment it was reached; no account name in the record
    assert attempts(world, email) == 3  # refused attempts are not recorded again: the table cannot be flooded by hammering


def test_a_miss_costs_a_password_check_like_a_hit(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import grademind_api.routes.auth as auth

    calls: list[str] = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda h, p: (calls.append(h[:10]), real(h, p))[1])
    c = app_with(world)
    attempt(c, f"ghost-{uuid.uuid4().hex}@college-one.in")
    assert len(calls) == 1  # no such account, and a hash was still checked (a dummy one)
    calls.clear()
    attempt(c, "admin2@college-one.in", PW)
    assert len(calls) == 1


def test_every_api_response_carries_the_security_headers(world: dict[str, Any]) -> None:
    c = world["client"]
    for r in (c.get("/health"), c.get("/api/me"), c.post("/api/auth/login", json={"email": "x@y.in", "password": "p"})):
        h = r.headers
        assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store"
        assert h["referrer-policy"] == "no-referrer" and "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["cross-origin-resource-policy"] == "same-origin"


def test_the_api_docs_exist_in_development_and_not_in_production(world: dict[str, Any]) -> None:
    assert world["client"].get("/openapi.json").status_code == 200
    prod = app_with(world, env=Env.PROD)
    assert [prod.get(p).status_code for p in ("/openapi.json", "/docs", "/redoc")] == [404, 404, 404]
    assert prod.get("/health").status_code == 200


def test_an_expired_or_forged_session_token_is_refused(world: dict[str, Any]) -> None:
    import jwt

    c = world["client"]
    secret = world["settings"].jwt_secret.get_secret_value()
    claims = {"sub": str(world["admin"]), "org": "00000000-0000-0000-0000-000000000000", "role": "admin"}
    now = datetime.now(UTC)
    expired = jwt.encode({**claims, "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)}, secret, algorithm="HS256")
    forged = jwt.encode({**claims, "iat": now, "exp": now + timedelta(hours=1)}, "x" * 40, algorithm="HS256")
    nonealg = jwt.encode({**claims, "iat": now, "exp": now + timedelta(hours=1)}, key="", algorithm="none")
    for tok in (expired, forged, nonealg):
        assert c.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
