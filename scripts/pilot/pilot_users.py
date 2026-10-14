#!/usr/bin/env python3
"""Create the pilot's accounts from a CSV through the running system's admin API (D31; no new product feature).

usage: scripts/pilot/pilot_users.py USERS.csv [--host H] [--connect ADDR] [--ca FILE] [--out FILE] [--dry-run]
         [--admin-from-env-file .env]      (or the environment: PILOT_ADMIN_EMAIL, PILOT_ADMIN_PASSWORD)

CSV (UTF-8, header row required):  email,name,role,exams
  role   admin | teacher | examiner
  exams  examiners only: exam names (or ids) separated by ';'. The examiner is assigned to each. Teachers and administrators
         see their college's exams by their role, so the column stays empty for them.

What it does, in order: validates the whole file (nothing is touched if any row is wrong); signs in as an administrator over
HTTPS (the same way the browser does, trusting only the pilot's CA root); creates each missing account with a random TEMPORARY
password (the person must choose their own at first sign-in); assigns examiners; writes the temporary passwords ONCE to a new
file with mode 600 (default pilot-data/temp-passwords-<time>.txt): hand them out in person and delete the file. Passwords are
never printed, logged or kept anywhere else. Run it again with the same CSV: accounts that exist are left alone (no new
password), missing ones are created, assignments already made are skipped. --dry-run validates the file and shows the plan only
(no network, no file).
"""

from __future__ import annotations

import argparse
import csv
import getpass
import http.client
import http.cookiejar
import json
import os
import re
import secrets
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROLES = ("admin", "teacher", "examiner")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_ROWS = 500


@dataclass(frozen=True)
class Row:
    line: int
    email: str
    name: str
    role: str
    exams: tuple[str, ...]


class CsvError(ValueError):
    pass


def parse_csv(path: Path) -> list[Row]:
    """All rows, or a CsvError listing EVERY problem (so the file is fixed in one go)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise CsvError(f"cannot read {path}: {type(e).__name__}") from e
    rd = csv.DictReader(text.splitlines())
    head = [h.strip().lower() for h in (rd.fieldnames or [])]
    missing = {"email", "name", "role"} - set(head)
    if missing:
        raise CsvError(f"the header row must have the columns email, name, role, exams (missing: {', '.join(sorted(missing))})")
    rows: list[Row] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(rd, start=2):
        rec = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
        if not any(rec.values()):
            continue
        email, name, role = rec.get("email", "").lower(), rec.get("name", ""), rec.get("role", "").lower()
        exams = tuple(x.strip() for x in rec.get("exams", "").split(";") if x.strip())
        if not EMAIL.match(email):
            problems.append(f"line {i}: '{email}' is not an email address")
        if email in seen:
            problems.append(f"line {i}: {email} is already on line {seen[email]}")
        seen.setdefault(email, i)
        if not name or len(name) > 200:
            problems.append(f"line {i}: the name is empty or over 200 characters")
        if role not in ROLES:
            problems.append(f"line {i}: role '{role}' must be one of {', '.join(ROLES)}")
        if exams and role != "examiner":
            problems.append(f"line {i}: only examiners are assigned to exams (role is '{role}')")
        rows.append(Row(i, email, name, role, exams))
    if not rows:
        problems.append("the file has no accounts")
    if len(rows) > MAX_ROWS:
        problems.append(f"more than {MAX_ROWS} accounts in one file")
    if problems:
        raise CsvError("\n".join(problems))
    return rows


class Api:
    """The web app's own door (sign in, then /api/proxy/...), as a browser uses it. Trusts only the given CA root."""

    def __init__(self, base: str, ca: str | None, connect: str | None) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPCookieProcessor(self.jar)]
        if base.startswith("https://"):
            ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
            target = connect

            class Conn(http.client.HTTPSConnection):
                def connect(self) -> None:  # connect to the machine's address, but present (and verify) the name people type
                    if not target:
                        return super().connect()
                    sock = socket.create_connection((target, self.port), self.timeout)
                    self.sock = ctx.wrap_socket(sock, server_hostname=self.host)

            class H(urllib.request.HTTPSHandler):
                def https_open(self, req):  # type: ignore[no-untyped-def]
                    return self.do_open(lambda host, **kw: Conn(host, context=ctx, **kw), req)

            handlers.append(H(context=ctx))
        self.opener = urllib.request.build_opener(*handlers)

    def call(self, method: str, path: str, body: object | None = None, form: dict[str, str] | None = None) -> tuple[int, object]:
        headers = {"accept": "application/json", "origin": self.base}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["content-type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)  # noqa: S310 - https (or a test's loopback http)
        try:
            with self.opener.open(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw and r.headers.get_content_type() == "application/json" else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw) if raw else None
            except ValueError:
                return e.code, None

    def sign_in(self, email: str, password: str) -> None:
        code, _ = self.call("POST", "/api/session", form={"email": email, "password": password})
        if code != 200 or not any(c.name for c in self.jar):
            raise SystemExit(
                "could not sign in as the administrator (wrong email or password, throttled, or the system is not reachable)"
            )
        code, me = self.call("GET", "/api/proxy/me")
        if code != 200 or not isinstance(me, dict) or me.get("role") != "admin":
            raise SystemExit("the account that signed in is not an administrator")


class PasswordFile:
    """A NEW file, mode 600, written as accounts are made (a crash half way must not lose a password already issued)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        self._w(f"# Temporary passwords made {stamp}. Hand each over in person, then DELETE THIS FILE.\n")
        self._w(
            "# Each person must choose their own password at their first sign-in.\n# email | name | role | temporary password\n"
        )

    def _w(self, s: str) -> None:
        os.write(self.fd, s.encode())
        os.fsync(self.fd)

    def add(self, row: Row, password: str) -> None:
        self._w(f"{row.email} | {row.name} | {row.role} | {password}\n")

    def close(self) -> None:
        os.close(self.fd)


def env_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    vals = [ln.split("=", 1)[1] for ln in path.read_text().splitlines() if ln.startswith(key + "=")]
    return vals[-1] if vals else ""


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("PILOT_ROOT", Path(__file__).resolve().parents[2]))
    data = Path(os.environ.get("PILOT_DATA", root / "pilot-data"))
    ap = argparse.ArgumentParser(description="Create pilot accounts from a CSV.")
    ap.add_argument("csv")
    ap.add_argument("--host", help="the name people type (default: GRADEMIND_HOST from .env)")
    ap.add_argument("--connect", help="address to connect to (default: GRADEMIND_HTTPS_BIND from .env)")
    ap.add_argument("--ca", help="the pilot CA root (default: pilot-data/ca/root.crt)")
    ap.add_argument("--base-url", help="full base URL instead of https://HOST:8443 (tests)")
    ap.add_argument("--admin-from-env-file", metavar="ENV", help="read the administrator's email and password from this .env")
    ap.add_argument("--admin-email")
    ap.add_argument("--out", help="the temporary-password file (default pilot-data/temp-passwords-<time>.txt)")
    ap.add_argument("--dry-run", action="store_true", help="validate the CSV and show the plan; no network, no file")
    a = ap.parse_args(argv)

    try:
        rows = parse_csv(Path(a.csv))
    except CsvError as e:
        print(f"{a.csv} was not used:\n{e}", file=sys.stderr)
        return 2
    if a.dry_run:
        print(f"DRY RUN: {len(rows)} account(s) in {a.csv}; nothing was contacted or written.")
        for r in rows:
            print(f"  would create {r.email} ({r.role}) {r.name!r}" + (f"; assign to: {', '.join(r.exams)}" if r.exams else ""))
        print("  each would get a random temporary password in a new mode-600 file; nobody's password is shown.")
        return 0

    envf = Path(a.admin_from_env_file) if a.admin_from_env_file else None
    admin_email = (
        a.admin_email or (env_value(envf, "GRADEMIND_ADMIN_EMAIL") if envf else "") or os.environ.get("PILOT_ADMIN_EMAIL", "")
    )
    admin_pw = (env_value(envf, "GRADEMIND_ADMIN_PASSWORD") if envf else "") or os.environ.get("PILOT_ADMIN_PASSWORD", "")
    if not admin_email:
        admin_email = input("Administrator email: ") if sys.stdin.isatty() else ""
    if not admin_pw and sys.stdin.isatty():
        admin_pw = getpass.getpass("Administrator password: ")
    if not (admin_email and admin_pw):
        print(
            "the administrator's email and password are needed: --admin-from-env-file .env, or PILOT_ADMIN_EMAIL and "
            "PILOT_ADMIN_PASSWORD, or type them",
            file=sys.stderr,
        )
        return 2
    env_file = root / ".env"
    host = a.host or env_value(env_file, "GRADEMIND_HOST")
    base = a.base_url or (f"https://{host}:8443" if host else "")
    if not base:
        print("--host is needed (or a .env with GRADEMIND_HOST)", file=sys.stderr)
        return 2
    ca = a.ca or str(data / "ca" / "root.crt")
    if base.startswith("https://") and not Path(ca).is_file():
        print(f"the CA root {ca} is missing: run scripts/pilot/pilot_setup.sh first, or give --ca", file=sys.stderr)
        return 2
    connect = a.connect or env_value(env_file, "GRADEMIND_HTTPS_BIND") or None

    api = Api(base, ca if base.startswith("https://") else None, connect)
    api.sign_in(admin_email, admin_pw)
    code, users = api.call("GET", "/api/proxy/users")
    code2, exams = api.call("GET", "/api/proxy/exams")
    if code != 200 or code2 != 200 or not isinstance(users, list) or not isinstance(exams, list):
        print("could not list the existing users and exams", file=sys.stderr)
        return 1
    by_email = {u["email"].lower(): u["id"] for u in users}
    by_name: dict[str, list[str]] = {}
    for ex_row in exams:
        by_name.setdefault(ex_row["name"], []).append(ex_row["id"])
    exam_ids = {ex_row["id"] for ex_row in exams}

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(a.out) if a.out else data / f"temp-passwords-{stamp}-{secrets.token_hex(3)}.txt"
    pw_file = PasswordFile(out)
    failures = made = existing = assigned = 0
    try:
        for r in rows:
            uid = by_email.get(r.email)
            if uid:
                existing += 1
                print(f"  {r.email}: already exists (left alone, no new password)")
            else:
                pw = secrets.token_urlsafe(14)
                code, body = api.call(
                    "POST", "/api/proxy/users", {"email": r.email, "display_name": r.name, "role": r.role, "password": pw}
                )
                if code != 201 or not isinstance(body, dict):
                    failures += 1
                    print(f"  {r.email}: NOT created (HTTP {code})", file=sys.stderr)
                    continue
                pw_file.add(r, pw)
                uid, made = body["id"], made + 1
                print(f"  {r.email}: created ({r.role})")
            for ex in r.exams:
                ids = [ex] if ex in exam_ids else by_name.get(ex, [])
                if len(ids) != 1:
                    failures += 1
                    print(
                        f"  {r.email}: exam '{ex}' "
                        + ("is ambiguous (several have that name: use its id)" if ids else "was not found"),
                        file=sys.stderr,
                    )
                    continue
                code, _ = api.call("POST", f"/api/proxy/exams/{ids[0]}/assignments", {"user_id": uid})
                if code in (200, 201, 204):
                    assigned += 1
                    print(f"  {r.email}: assigned to '{ex}'")
                elif code == 409:
                    print(f"  {r.email}: already assigned to '{ex}'")
                else:
                    failures += 1
                    print(f"  {r.email}: could not be assigned to '{ex}' (HTTP {code})", file=sys.stderr)
    finally:
        pw_file.close()
    if made == 0:
        out.unlink(missing_ok=True)  # nothing was issued: do not leave an empty file that looks like a list of passwords
    print(f"{made} created, {existing} already existed, {assigned} assignment(s) made, {failures} problem(s).")
    if made:
        print(f"Temporary passwords: {out}  (mode 600). Hand them out in person, then delete the file. Shown nowhere else.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
