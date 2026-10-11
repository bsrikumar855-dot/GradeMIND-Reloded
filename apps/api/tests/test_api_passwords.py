"""5.0 (D30.3): self-service password change, admin reset with a forced change, session invalidation, throttling, and that no
password ever reaches a log, an audit row or an error body. Real Postgres."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from conftest import PW, login, needs_db
from sqlalchemy import select

from grademind_core.db.models import AuditLog, User

pytestmark = needs_db
NEW1 = "Brand-New-Passphrase-4471"
NEW2 = "Another-Fresh-Passphrase-9302"


def make_user(world: dict[str, Any], role: str = "examiner", who: str = "admin") -> dict[str, str]:
    email = f"pw-{uuid.uuid4().hex[:10]}@college-one.in"
    r = world["client"].post(
        "/api/users",
        json={"email": email, "display_name": "Pw Test", "role": role, "password": PW, "must_change_password": False},
        headers=login(world, who),
    )
    assert r.status_code == 201, r.text
    return {"email": email, "id": r.json()["id"]}


def sign_in(world: dict[str, Any], email: str, password: str) -> Any:
    return world["client"].post("/api/auth/login", json={"email": email, "password": password})


def bearer(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def change(world: dict[str, Any], tok: str, cur: str, new: str) -> Any:
    return world["client"].post(
        "/api/auth/change-password", json={"current_password": cur, "new_password": new}, headers=bearer(tok)
    )


def audit(world: dict[str, Any], action: str, entity_id: str) -> list[AuditLog]:
    with world["sessions"]() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == action, AuditLog.entity_id == entity_id)))


def test_changing_your_password_ends_every_other_session_and_returns_a_fresh_one(world: dict[str, Any]) -> None:
    u = make_user(world)
    c = world["client"]
    a = sign_in(world, u["email"], PW).json()["access_token"]
    b = sign_in(world, u["email"], PW).json()["access_token"]  # a second device
    r = change(world, a, PW, NEW1)
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    fresh = r.json()["access_token"]
    assert c.get("/api/me", headers=bearer(a)).status_code == 401  # the token that made the change is replaced...
    assert c.get("/api/me", headers=bearer(b)).status_code == 401  # ...and so is every other session
    assert c.get("/api/me", headers=bearer(fresh)).status_code == 200
    assert sign_in(world, u["email"], PW).status_code == 401 and sign_in(world, u["email"], NEW1).status_code == 200
    with world["sessions"]() as s:
        row = s.get(User, uuid.UUID(u["id"]))
        assert row is not None and row.password_hash.startswith("$argon2id$") and row.password_changed_at is not None
    (a1,) = audit(world, "auth.password_changed", u["id"])
    assert a1.details == {"was_forced": False} and NEW1 not in str(a1.details) and PW not in str(a1.details)


def test_the_current_password_is_required_and_wrong_guesses_are_throttled(world: dict[str, Any]) -> None:
    u = make_user(world)
    tok = sign_in(world, u["email"], PW).json()["access_token"]
    for _ in range(5):
        r = change(world, tok, "not-the-password-at-all", NEW1)
        assert r.status_code == 403 and r.json()["error"]["code"] == "invalid_current_password"
    r = change(world, tok, PW, NEW1)  # even the right one is refused while the limit is in force
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 900
    assert world["client"].get("/api/me", headers=bearer(tok)).status_code == 200  # the password was NOT changed
    assert audit(world, "auth.password_change_throttled", u["id"])


@pytest.mark.parametrize(
    ("new", "fragment"),
    [("short-one", "at least 12"), (PW, "different"), ("x" * 201, "invalid"), ("aaaaaaaaaaaaaaaa", "different characters")],
)
def test_weak_new_passwords_are_refused_with_a_reason(world: dict[str, Any], new: str, fragment: str) -> None:
    u = make_user(world)
    tok = sign_in(world, u["email"], PW).json()["access_token"]
    r = change(world, tok, PW, new)
    assert r.status_code in (422,) and fragment in r.json()["error"]["message"]
    assert new not in r.text[:400] or len(new) > 50  # the refused password is not echoed back
    assert world["client"].get("/api/me", headers=bearer(tok)).status_code == 200


def test_a_password_containing_the_email_is_refused_and_the_endpoint_needs_a_session(world: dict[str, Any]) -> None:
    u = make_user(world)
    tok = sign_in(world, u["email"], PW).json()["access_token"]
    local = u["email"].split("@")[0]
    assert change(world, tok, PW, f"{local}-Extra-9921").status_code == 422
    assert (
        world["client"].post("/api/auth/change-password", json={"current_password": PW, "new_password": NEW1}).status_code == 401
    )


def test_only_an_admin_resets_and_the_temporary_password_only_gets_you_as_far_as_choosing_your_own(world: dict[str, Any]) -> None:
    u = make_user(world)
    c = world["client"]
    old = sign_in(world, u["email"], PW).json()["access_token"]
    url = f"/api/users/{u['id']}/reset-password"
    for who in ("teacher", "exA"):
        assert c.post(url, headers=login(world, who)).status_code == 403
    assert c.post(f"/api/users/{world['admin']}/reset-password", headers=login(world, "admin")).status_code == 409  # not your own
    assert (
        c.post(f"/api/users/{world['ex2']}/reset-password", headers=login(world, "admin")).status_code == 404
    )  # another organisation
    r = c.post(url, headers=login(world, "admin"))
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert len(temp) >= 16 and temp != PW
    assert c.get("/api/me", headers=bearer(old)).status_code == 401  # their sessions ended at once
    assert sign_in(world, u["email"], PW).status_code == 401  # the old password is gone
    ok = sign_in(world, u["email"], temp)
    assert ok.status_code == 200 and ok.json()["must_change_password"] is True
    tok = ok.json()["access_token"]
    me = c.get("/api/me", headers=bearer(tok)).json()
    assert me["must_change_password"] is True  # /me still works, so the web app knows where to send them
    blocked = c.get("/api/exams", headers=bearer(tok))
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "password_change_required"
    assert c.post("/api/exams", json={"name": "x", "subject": "y", "total_marks": "1"}, headers=bearer(tok)).status_code == 403
    done = change(world, tok, temp, NEW2)
    assert done.status_code == 200
    assert (
        c.get("/api/exams", headers=bearer(done.json()["access_token"])).status_code == 200
    )  # free to work with their own password
    assert sign_in(world, u["email"], temp).status_code == 401
    (reset_row,) = audit(world, "user.password_reset", u["id"])
    assert (
        reset_row.details == {"role": "examiner"} and temp not in str(reset_row.details) and reset_row.actor_id == world["admin"]
    )
    (chg,) = audit(world, "auth.password_changed", u["id"])
    assert chg.details == {"was_forced": True}


def test_no_password_reaches_a_log_or_an_error_body(world: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    u = make_user(world)
    c = world["client"]
    tok = sign_in(world, u["email"], PW).json()["access_token"]
    with caplog.at_level(logging.DEBUG):
        change(world, tok, "wrong-current-password-1", NEW1)
        c.post("/api/auth/change-password", json={"current_password": PW}, headers=bearer(tok))  # missing field -> 422
        c.post(
            "/api/auth/change-password",
            content='{"current_password": "' + PW + '", "new_password": "' + NEW1,
            headers={**bearer(tok), "content-type": "application/json"},
        )
        r = change(world, tok, PW, NEW1)
        temp = c.post(f"/api/users/{make_user(world)['id']}/reset-password", headers=login(world, "admin")).json()[
            "temporary_password"
        ]
    assert r.status_code == 200
    for secret in (PW, NEW1, "wrong-current-password-1", temp):
        assert secret not in caplog.text, "a password reached a log record"
    bad = c.post(
        "/api/auth/change-password", json={"current_password": PW, "new_password": 5}, headers=bearer(r.json()["access_token"])
    )
    assert bad.status_code == 422 and PW not in bad.text
    with world["sessions"]() as s:
        rows = s.scalars(
            select(AuditLog).where(
                AuditLog.action.in_(["auth.password_changed", "user.password_reset", "auth.password_change_throttled"])
            )
        )
        assert all(secret not in str(x.details) for x in rows for secret in (PW, NEW1, temp))


def test_a_token_from_before_this_change_without_the_version_claim_still_works_until_a_password_changes(
    world: dict[str, Any],
) -> None:
    u = make_user(world)
    secret = world["settings"].jwt_secret.get_secret_value()
    with world["sessions"]() as s:
        row = s.get(User, uuid.UUID(u["id"]))
        assert row is not None
        claims = {"sub": str(row.id), "org": str(row.org_id), "role": row.role.value}
    now = datetime.now(UTC)
    legacy = jwt.encode({**claims, "iat": now, "exp": now + timedelta(hours=1)}, secret, algorithm="HS256")  # no "tv"
    assert world["client"].get("/api/me", headers=bearer(legacy)).status_code == 200
    tok = sign_in(world, u["email"], PW).json()["access_token"]
    assert change(world, tok, PW, NEW1).status_code == 200
    assert world["client"].get("/api/me", headers=bearer(legacy)).status_code == 401


def test_an_account_the_administrator_creates_must_choose_its_own_password_at_first_sign_in(world: dict[str, Any]) -> None:
    """D31.4: same as after a reset: the given password gets the person as far as the change page and nowhere else."""
    email = f"first-{uuid.uuid4().hex[:10]}@college-one.in"
    c = world["client"]
    r = c.post(
        "/api/users",
        json={"email": email, "display_name": "First Timer", "role": "examiner", "password": PW},
        headers=login(world, "admin"),
    )
    assert r.status_code == 201, r.text
    tok = sign_in(world, email, PW).json()
    assert tok["must_change_password"] is True
    assert c.get("/api/exams", headers=bearer(tok["access_token"])).status_code == 403
    assert c.get("/api/me", headers=bearer(tok["access_token"])).json()["must_change_password"] is True
    assert change(world, tok["access_token"], PW, NEW1).status_code == 200
    again = sign_in(world, email, NEW1).json()
    assert (
        again["must_change_password"] is False and c.get("/api/exams", headers=bearer(again["access_token"])).status_code == 200
    )
    with world["sessions"]() as s:
        a = s.scalars(select(AuditLog).where(AuditLog.action == "user.create", AuditLog.entity_id == r.json()["id"])).one()
    assert a.details["must_change_password"] is True and PW not in str(a.details)


def test_the_hide_machine_reading_choice_is_kept_on_the_account_not_the_browser(world: dict[str, Any]) -> None:
    """D31.3: set on one device, seen on another; only that field can be set; not while a temporary password is in force."""
    u = make_user(world)
    c = world["client"]
    first = sign_in(world, u["email"], PW).json()["access_token"]
    assert c.get("/api/me", headers=bearer(first)).json()["machine_reading_hidden"] is False  # shown by default
    r = c.put("/api/me/preferences", json={"machine_reading_hidden": True}, headers=bearer(first))
    assert r.status_code == 200 and r.json()["machine_reading_hidden"] is True
    other_device = sign_in(world, u["email"], PW).json()["access_token"]
    assert c.get("/api/me", headers=bearer(other_device)).json()["machine_reading_hidden"] is True
    assert (
        c.put("/api/me/preferences", json={"machine_reading_hidden": False}, headers=bearer(other_device)).json()[
            "machine_reading_hidden"
        ]
        is False
    )
    assert c.get("/api/me", headers=bearer(first)).json()["machine_reading_hidden"] is False
    assert c.put("/api/me/preferences", json={}, headers=bearer(first)).status_code == 422
    assert c.put("/api/me/preferences", json={"machine_reading_hidden": True}).status_code == 401
    # someone holding a temporary password can do nothing but change it
    temp = c.post(f"/api/users/{u['id']}/reset-password", headers=login(world, "admin")).json()["temporary_password"]
    held = sign_in(world, u["email"], temp).json()["access_token"]
    assert c.put("/api/me/preferences", json={"machine_reading_hidden": True}, headers=bearer(held)).status_code == 403
