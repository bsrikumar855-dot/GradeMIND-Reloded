"""4.1 (D29): user administration and examiner assignment through the API, and the role checks around them.
Real Postgres. Admin only, own organisation only, audited without passwords, and an examiner sees only what is assigned."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import PW, login, needs_db
from sqlalchemy import select

from grademind_core.db.models import AuditLog

pytestmark = needs_db
NEW_PW = "a-new-temporary-pass-1"


def make_exam(w: dict[str, Any], who: str = "admin", name: str = "Users exam") -> str:
    r = w["client"].post("/api/exams", json={"name": name, "subject": "S", "total_marks": "10"}, headers=login(w, who))
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def new_user(w: dict[str, Any], email: str, role: str = "examiner", password: str = NEW_PW, who: str = "admin") -> Any:
    return w["client"].post(
        "/api/users",
        json={"email": email, "display_name": "New Person", "role": role, "password": password},
        headers=login(w, who),
    )


def actions(w: dict[str, Any], entity_id: str) -> list[str]:
    with w["sessions"]() as s:
        return list(
            s.scalars(
                select(AuditLog.action)
                .where(AuditLog.entity_id == entity_id, AuditLog.action != "auth.login")
                .order_by(AuditLog.at)
            )
        )


@pytest.mark.parametrize("who", ["teacher", "exA"])
def test_only_an_admin_manages_users(world: dict[str, Any], who: str) -> None:
    c, h = world["client"], login(world, who)
    assert c.get("/api/users", headers=h).status_code == 403
    assert new_user(world, "nope@college-one.in", who=who).status_code == 403
    uid = str(world["exB"])
    assert c.post(f"/api/users/{uid}/deactivate", headers=h).status_code == 403
    assert c.post(f"/api/users/{uid}/activate", headers=h).status_code == 403
    exam = make_exam(world)
    assert c.get(f"/api/exams/{exam}/assignments", headers=h).status_code == 403
    assert c.post(f"/api/exams/{exam}/assignments", json={"user_id": uid}, headers=h).status_code == 403
    assert c.delete(f"/api/exams/{exam}/assignments/{uid}", headers=h).status_code == 403


def test_users_endpoints_need_a_session(world: dict[str, Any]) -> None:
    c = world["client"]
    assert c.get("/api/users").status_code == 401
    assert c.post("/api/users", json={}).status_code == 401


def test_an_admin_creates_a_user_who_can_sign_in_and_nothing_secret_is_returned_or_audited(world: dict[str, Any]) -> None:
    c = world["client"]
    r = new_user(world, "  New.Examiner@College-One.in ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "new.examiner@college-one.in" and body["role"] == "examiner" and body["is_active"] is True
    assert "password" not in body and "password_hash" not in body and NEW_PW not in r.text
    ok = c.post("/api/auth/login", json={"email": "new.examiner@college-one.in", "password": NEW_PW})
    assert ok.status_code == 200
    me = c.get("/api/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).json()
    assert me["role"] == "examiner" and me["display_name"] == "New Person"
    assert actions(world, body["id"]) == ["user.create"]
    with world["sessions"]() as s:
        details = s.scalars(
            select(AuditLog.details).where(AuditLog.entity_id == body["id"], AuditLog.action == "user.create")
        ).all()
    assert details == [{"role": "examiner"}] and NEW_PW not in str(details)


@pytest.mark.parametrize(
    ("email", "role", "password", "status"),
    [
        ("dup@college-one.in", "examiner", NEW_PW, 409),  # created first below, then repeated
        ("short@college-one.in", "examiner", "short-pass", 422),  # under 12 characters
        ("badrole@college-one.in", "superuser", NEW_PW, 422),
        ("not-an-email", "examiner", NEW_PW, 422),
    ],
)
def test_bad_input_is_refused(world: dict[str, Any], email: str, role: str, password: str, status: int) -> None:
    if status == 409:
        assert new_user(world, email).status_code == 201
    r = new_user(world, email, role=role, password=password)
    assert r.status_code == status
    if status == 409:
        assert r.json()["error"]["code"] == "email_in_use"
    else:
        assert PW not in r.text and password not in r.text  # the offending input is not echoed back


def test_the_user_list_is_this_organisations_only(world: dict[str, Any]) -> None:
    rows = world["client"].get("/api/users", headers=login(world, "admin")).json()
    ids = {r["id"] for r in rows}
    assert str(world["admin"]) in ids and str(world["exA"]) in ids
    assert str(world["admin2"]) not in ids and str(world["ex2"]) not in ids
    assert all("password" not in r and "password_hash" not in r for r in rows)
    other = world["client"].get("/api/users", headers=login(world, "admin2")).json()
    assert {r["id"] for r in other} == {str(world["admin2"]), str(world["ex2"])}


def test_deactivation_takes_effect_at_once_and_is_reversible_and_audited(world: dict[str, Any]) -> None:
    c = world["client"]
    uid = new_user(world, "temp.examiner@college-one.in").json()["id"]
    tok = c.post("/api/auth/login", json={"email": "temp.examiner@college-one.in", "password": NEW_PW}).json()["access_token"]
    mine = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/me", headers=mine).status_code == 200
    admin = login(world, "admin")
    assert c.post(f"/api/users/{uid}/deactivate", headers=admin).json()["is_active"] is False
    assert c.get("/api/me", headers=mine).status_code == 401  # the token they already hold stops working
    assert c.post("/api/auth/login", json={"email": "temp.examiner@college-one.in", "password": NEW_PW}).status_code == 401
    assert c.post(f"/api/users/{uid}/deactivate", headers=admin).status_code == 200  # idempotent...
    assert actions(world, uid) == ["user.create", "user.deactivate"]  # ...and audited once
    assert c.post(f"/api/users/{uid}/activate", headers=admin).json()["is_active"] is True
    assert c.get("/api/me", headers=mine).status_code == 200
    assert actions(world, uid) == ["user.create", "user.deactivate", "user.activate"]


def test_you_cannot_deactivate_yourself_or_touch_another_organisation(world: dict[str, Any]) -> None:
    c, admin = world["client"], login(world, "admin")
    r = c.post(f"/api/users/{world['admin']}/deactivate", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "own_account"
    assert c.post(f"/api/users/{world['ex2']}/deactivate", headers=admin).status_code == 404
    assert c.post(f"/api/users/{uuid.uuid4()}/activate", headers=admin).status_code == 404


def test_assigning_examiners_and_what_an_examiner_then_sees(world: dict[str, Any]) -> None:
    c, admin = world["client"], login(world, "admin")
    mine, other = make_exam(world, name="Assigned exam"), make_exam(world, name="Not assigned")
    ex = login(world, "exA")
    assert c.get("/api/exams", headers=ex).json() == [] or all(
        e["id"] not in (mine, other) for e in c.get("/api/exams", headers=ex).json()
    )
    assert c.get(f"/api/exams/{mine}", headers=ex).status_code == 404  # not assigned: its existence is not revealed

    assert c.post(f"/api/exams/{mine}/assignments", json={"user_id": str(world["exA"])}, headers=admin).status_code == 204
    assigned = c.get(f"/api/exams/{mine}/assignments", headers=admin).json()
    assert [a["id"] for a in assigned] == [str(world["exA"])]
    visible = {e["id"] for e in c.get("/api/exams", headers=ex).json()}
    assert mine in visible and other not in visible
    assert c.get(f"/api/exams/{mine}", headers=ex).status_code == 200
    assert c.get(f"/api/exams/{other}", headers=ex).status_code == 404
    assert c.get(f"/api/exams/{other}/submissions", headers=ex).status_code == 404

    # an examiner never sees the audit trail, even of the exam they are assigned to; a teacher does
    assert c.get(f"/api/exams/{mine}/audit", headers=ex).status_code == 403
    assert c.get(f"/api/exams/{mine}/audit", headers=login(world, "teacher")).status_code == 200

    assert c.delete(f"/api/exams/{mine}/assignments/{world['exA']}", headers=admin).status_code == 204
    assert c.get(f"/api/exams/{mine}", headers=ex).status_code == 404  # access ends at once
    assert c.get(f"/api/exams/{mine}/assignments", headers=admin).json() == []
    assert c.delete(f"/api/exams/{mine}/assignments/{world['exA']}", headers=admin).status_code == 204  # idempotent
    log = actions(world, mine)
    assert (
        log.count("exam.assign") == 1 and log.count("exam.unassign") == 1
    )  # the repeat changed nothing, so it is not audited again


def test_only_active_examiners_of_this_organisation_can_be_assigned(world: dict[str, Any]) -> None:
    c, admin = world["client"], login(world, "admin")
    exam = make_exam(world, name="Assign rules")
    for who in ("teacher", "admin", "ex2"):  # not an examiner / another organisation
        r = c.post(f"/api/exams/{exam}/assignments", json={"user_id": str(world[who])}, headers=admin)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_assignee"
    uid = new_user(world, "gone.examiner@college-one.in").json()["id"]
    c.post(f"/api/users/{uid}/deactivate", headers=admin)
    assert c.post(f"/api/exams/{exam}/assignments", json={"user_id": uid}, headers=admin).status_code == 422
    # an admin of another organisation cannot reach this exam at all
    other = login(world, "admin2")
    assert c.get(f"/api/exams/{exam}/assignments", headers=other).status_code == 404
    assert c.post(f"/api/exams/{exam}/assignments", json={"user_id": str(world["ex2"])}, headers=other).status_code == 404
    assert c.delete(f"/api/exams/{exam}/assignments/{world['exA']}", headers=other).status_code == 404
