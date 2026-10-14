"""D31 scripts/pilot/pilot_users.py against a fake of the web app's sign-in and admin API (plain HTTP on loopback).
NOT covered here: TLS with the real CA and the real API (CI job `pilot-automation` runs it against a real stack)."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import threading
import urllib.parse
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot" / "pilot_users.py"
ADMIN_PW = "the-admin-password-123"


class State:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {
            "admin@example.edu": {"id": str(uuid.uuid4()), "email": "admin@example.edu", "role": "admin"}
        }
        self.exams = [
            {"id": str(uuid.uuid4()), "name": "Chemistry"},
            {"id": str(uuid.uuid4()), "name": "Biology"},
            {"id": str(uuid.uuid4()), "name": "Twin"},
            {"id": str(uuid.uuid4()), "name": "Twin"},
        ]
        self.assignments: set[tuple[str, str]] = set()
        self.created_bodies: list[dict[str, Any]] = []
        self.origins: list[str | None] = []
        self.fail_create_for: set[str] = set()


def make_handler(st: State) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def _json(self, code: int, body: Any = None) -> None:
            raw = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            if raw:
                self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authed(self) -> bool:
            return "gm_session=ok" in (self.headers.get("cookie") or "")

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n)
            st.origins.append(self.headers.get("origin"))
            if self.path == "/api/session":
                form = urllib.parse.parse_qs(body.decode())
                if form.get("password") == [ADMIN_PW] and form.get("email") == ["admin@example.edu"]:
                    self.send_response(303)
                    self.send_header("location", "/")
                    self.send_header("set-cookie", "gm_session=ok; Path=/; HttpOnly")
                    self.send_header("content-length", "0")
                    self.end_headers()
                else:
                    self.send_response(303)
                    self.send_header("location", "/login?error=invalid")
                    self.send_header("content-length", "0")
                    self.end_headers()
                return
            if not self._authed():
                return self._json(401, {"error": {"code": "unauthorized"}})
            data = json.loads(body or b"{}")
            if self.path == "/api/proxy/users":
                if data["email"] in st.fail_create_for:
                    return self._json(500, {"error": {"code": "boom"}})
                if data["email"] in st.users:
                    return self._json(409, {"error": {"code": "email_in_use"}})
                st.created_bodies.append(data)
                u = {"id": str(uuid.uuid4()), "email": data["email"], "role": data["role"]}
                st.users[data["email"]] = u
                return self._json(201, u)
            parts = self.path.strip("/").split("/")
            if len(parts) == 5 and parts[2] == "exams" and parts[4] == "assignments":
                key = (parts[3], data["user_id"])
                if key in st.assignments:
                    return self._json(409, {"error": {"code": "already_assigned"}})
                st.assignments.add(key)
                return self._json(204)
            self._json(404)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                return self._json(200, {"ok": True})
            if not self._authed():
                return self._json(401)
            if self.path == "/api/proxy/me":
                return self._json(200, {"role": "admin"})
            if self.path == "/api/proxy/users":
                return self._json(200, list(st.users.values()))
            if self.path == "/api/proxy/exams":
                return self._json(200, st.exams)
            self._json(404)

    return H


@pytest.fixture
def api() -> Iterator[tuple[str, State]]:
    st = State()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(st))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", st
    srv.shutdown()
    srv.server_close()


def run(tmp: Path, csv_text: str, base: str | None, *extra: str, pw: str = ADMIN_PW) -> subprocess.CompletedProcess[str]:
    f = tmp / "users.csv"
    f.write_text(csv_text, encoding="utf-8")
    cmd = [sys.executable, str(SCRIPT), str(f)]
    if base:
        cmd += ["--base-url", base]
    env = {
        "PATH": "/usr/bin:/bin",
        "PILOT_ROOT": str(tmp),
        "PILOT_DATA": str(tmp / "pilot-data"),
        "PILOT_ADMIN_EMAIL": "admin@example.edu",
        "PILOT_ADMIN_PASSWORD": pw,
    }
    return subprocess.run([*cmd, *extra], capture_output=True, text=True, env=env, timeout=60, check=False)


CSV = (
    "email,name,role,exams\n"
    "Tara@Example.edu,Tara Teacher,teacher,\n"
    "ex1@example.edu,Examiner One,examiner,Chemistry;Biology\n"
    "ex2@example.edu,Examiner Two,examiner,Chemistry\n"
    "ad2@example.edu,Second Admin,admin,\n"
)


def pw_file(tmp: Path) -> Path:
    (f,) = list((tmp / "pilot-data").glob("temp-passwords-*.txt"))
    return f


def test_it_creates_accounts_assigns_examiners_and_hands_the_passwords_over_only_in_a_private_file(
    tmp_path: Path, api: tuple[str, State]
) -> None:
    base, st = api
    r = run(tmp_path, CSV, base)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "4 created, 0 already existed, 3 assignment(s) made, 0 problem(s)" in r.stdout
    assert {u["role"] for e, u in st.users.items() if e != "admin@example.edu"} == {"teacher", "examiner", "admin"}
    assert "tara@example.edu" in st.users  # e-mail addresses are lower-cased
    chem, bio = (e["id"] for e in st.exams[:2])
    ids = {e: u["id"] for e, u in st.users.items()}
    assert st.assignments == {(chem, ids["ex1@example.edu"]), (bio, ids["ex1@example.edu"]), (chem, ids["ex2@example.edu"])}
    assert set(st.origins) == {base}  # the sign-in carries the same origin the web app checks
    f = pw_file(tmp_path)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    body = [ln for ln in f.read_text().splitlines() if not ln.startswith("#")]
    pws = [ln.split(" | ")[3] for ln in body]
    assert len(body) == 4 and len(set(pws)) == 4 and all(len(p) >= 12 for p in pws)  # distinct, long enough for the API's rule
    assert sorted(p["password"] for p in st.created_bodies) == sorted(pws)  # the file has exactly what the API was given
    assert not any(p in r.stdout + r.stderr for p in pws) and ADMIN_PW not in r.stdout + r.stderr  # no password on screen
    assert "DELETE THIS FILE" in f.read_text() and "Hand them out in person" in r.stdout


def test_running_it_again_leaves_existing_people_alone_and_issues_no_new_passwords(
    tmp_path: Path, api: tuple[str, State]
) -> None:
    base, st = api
    assert run(tmp_path, CSV, base).returncode == 0
    n_created = len(st.created_bodies)
    (tmp_path / "pilot-data" / next((tmp_path / "pilot-data").glob("temp-passwords-*")).name).unlink()
    r = run(tmp_path, CSV + "ex3@example.edu,Examiner Three,examiner,Biology\n", base)
    assert (
        r.returncode == 0 and "1 created, 4 already existed, 1 assignment(s) made" in r.stdout and "already assigned" in r.stdout
    )
    assert len(st.created_bodies) == n_created + 1
    pw_file(tmp_path).unlink()  # (the new person's password was handed over)
    again = run(tmp_path, CSV, base)
    assert "0 created, 4 already existed" in again.stdout and not list(
        (tmp_path / "pilot-data").glob("temp-passwords-*")
    )  # no empty password file left behind
    assert "no new password" in again.stdout


def test_a_bad_file_is_rejected_whole_before_any_contact(tmp_path: Path, api: tuple[str, State]) -> None:
    base, st = api
    bad = (
        "email,name,role,exams\nnot-an-email,A,examiner,\na@x.edu,,examiner,\nb@x.edu,B,boss,\n"
        "c@x.edu,C,teacher,Chemistry\na@x.edu,D,examiner,\n"
    )
    r = run(tmp_path, bad, base)
    assert r.returncode == 2
    for part in (
        "line 2: 'not-an-email' is not an email",
        "line 3: the name is empty",
        "line 4: role 'boss'",
        "line 5: only examiners are assigned",
        "line 6: a@x.edu is already on line 3",
    ):
        assert part in r.stderr, part
    assert st.created_bodies == [] and not (tmp_path / "pilot-data").exists()
    assert run(tmp_path, "email,role\na@b.edu,admin\n", base).returncode == 2
    assert run(tmp_path, "email,name,role,exams\n", base).returncode == 2


def test_dry_run_validates_and_shows_the_plan_without_network_or_files(tmp_path: Path) -> None:
    r = run(tmp_path, CSV, None, "--dry-run", pw="")
    assert (
        r.returncode == 0
        and "DRY RUN: 4 account(s)" in r.stdout
        and "would create ex1@example.edu (examiner)" in r.stdout
        and "assign to: Chemistry, Biology" in r.stdout
    )
    assert not (tmp_path / "pilot-data").exists()
    assert run(tmp_path, "email,name,role,exams\nbad,B,admin,\n", None, "--dry-run", pw="").returncode == 2


def test_a_wrong_admin_password_or_a_non_admin_creates_nothing_and_the_password_is_not_echoed(
    tmp_path: Path, api: tuple[str, State]
) -> None:
    base, st = api
    r = run(tmp_path, CSV, base, pw="wrong-password-value")
    assert r.returncode != 0 and "could not sign in" in r.stderr and "wrong-password-value" not in r.stdout + r.stderr
    assert st.created_bodies == [] and not (tmp_path / "pilot-data").exists()


def test_problems_with_one_row_are_reported_and_the_rest_still_done(tmp_path: Path, api: tuple[str, State]) -> None:
    base, st = api
    st.fail_create_for.add("ex1@example.edu")
    csv = (
        "email,name,role,exams\nex1@example.edu,One,examiner,Chemistry\nex2@example.edu,Two,examiner,Nowhere\n"
        "ex3@example.edu,Three,examiner,Twin\nex4@example.edu,Four,examiner,Biology\n"
    )
    r = run(tmp_path, csv, base)
    assert r.returncode == 1
    assert (
        "ex1@example.edu: NOT created (HTTP 500)" in r.stderr
        and "exam 'Nowhere' was not found" in r.stderr
        and "exam 'Twin' is ambiguous" in r.stderr
    )
    assert "ex4@example.edu: assigned to 'Biology'" in r.stdout
    body = [ln for ln in pw_file(tmp_path).read_text().splitlines() if not ln.startswith("#")]
    assert len(body) == 3 and not any("ex1@example.edu" in ln for ln in body)  # no password for an account that was not made
    twin = st.exams[2]["id"]
    assert not any(a[0] == twin for a in st.assignments)  # an ambiguous name is never guessed
    assert (
        run(tmp_path, csv, base, "--out", str(pw_file(tmp_path))).returncode != 0
    )  # an existing password file is never overwritten


def test_an_exam_can_be_given_by_id_and_the_password_file_is_new_every_time(tmp_path: Path, api: tuple[str, State]) -> None:
    base, st = api
    twin = st.exams[2]["id"]
    r = run(
        tmp_path, f"email,name,role,exams\nex1@example.edu,One,examiner,{twin}\n", base, "--out", str(tmp_path / "out" / "p.txt")
    )
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out" / "p.txt").exists() and stat.S_IMODE((tmp_path / "out" / "p.txt").stat().st_mode) == 0o600
    assert any(a[0] == twin for a in st.assignments)


def test_the_tls_path_refuses_without_the_pilot_ca(tmp_path: Path) -> None:
    r = run(tmp_path, CSV, None, "--host", "grademind.lan")
    assert r.returncode == 2 and "CA root" in r.stderr and "pilot_setup.sh" in r.stderr
