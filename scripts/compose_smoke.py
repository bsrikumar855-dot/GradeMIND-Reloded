"""End-to-end smoke test against a running `docker compose` stack (stdlib only; used locally and in CI).

health endpoints -> login as the bootstrapped admin -> create exam -> upload a synthetic PDF -> follow the ingest job
over SSE until the real worker (Celery + Redis) finishes it -> fetch the source through its signed URL.
Reads GRADEMIND_ADMIN_EMAIL / GRADEMIND_ADMIN_PASSWORD from the environment or .env. Exit code 0 = pass.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "helpers"))
from pdfgen import make_pdf  # noqa: E402 - stdlib-only test helper (synthetic PDFs)

API = os.environ.get("SMOKE_API", "http://127.0.0.1:8000")
WEB = os.environ.get("SMOKE_WEB", "http://127.0.0.1:3100")
REQUIRE_OCR = os.environ.get("SMOKE_REQUIRE_OCR", "1") == "1"


def env(key: str) -> str:
    if key in os.environ:
        return os.environ[key]
    for line in Path(".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    raise SystemExit(f"{key} not set")


def call(method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(API + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(cond: bool, what: str) -> None:
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        sys.exit(1)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:
        return None


def web(path: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(WEB + path, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with opener.open(req, timeout=30) as r:
            # React separates adjacent text nodes with <!-- --> in server HTML
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode().replace("<!-- -->", "")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode(errors="replace")


def header_checks() -> None:
    """4.6: the security headers the browser relies on, on the web app and on the API."""
    req = urllib.request.Request(WEB + "/login")
    with urllib.request.urlopen(req, timeout=30) as r:
        h = {k.lower(): v for k, v in r.headers.items()}
    csp = h.get("content-security-policy", "")
    has_policy = "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "'nonce-" in csp
    check(has_policy, "web: Content-Security-Policy with a per-request nonce")
    check("'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0], "web: scripts are not allowed inline")
    check(h.get("x-content-type-options") == "nosniff" and h.get("x-frame-options") == "DENY", "web: nosniff and no framing")
    policies = h.get("referrer-policy") == "same-origin" and h.get("cross-origin-opener-policy") == "same-origin"
    check(policies, "web: referrer and opener policy")
    check("x-powered-by" not in h, "web: does not announce its framework")
    with urllib.request.urlopen(API + "/health", timeout=10) as r:
        a = {k.lower(): v for k, v in r.headers.items()}
    check(a.get("x-content-type-options") == "nosniff" and a.get("cache-control") == "no-store", "api: nosniff and no-store")
    check("frame-ancestors 'none'" in a.get("content-security-policy", ""), "api: Content-Security-Policy")
    code, _ = call("GET", "/openapi.json")
    check(code == 404, "api: the interactive docs are not served in production")


def web_checks(exam_name: str) -> None:
    code, _, html = web("/login")
    check(code == 200 and "Sign in to GradeMIND" in html, "web /login renders")
    code, h, _ = web("/")
    loc = h.get("location", "")
    check(
        code in (303, 307) and (loc.startswith("/login") or loc.startswith(WEB + "/login")),
        f"web / without a session redirects to /login ({loc!r})",
    )
    form = urllib.parse.urlencode({"email": env("GRADEMIND_ADMIN_EMAIL"), "password": "wrong-password"}).encode()
    code, h, _ = web("/api/session", form, {"content-type": "application/x-www-form-urlencoded", "origin": WEB})
    check(code == 303 and "error=invalid" in h.get("location", ""), "web login with a wrong password is refused")
    code, _, _ = web(
        "/api/session", form, {"content-type": "application/x-www-form-urlencoded", "origin": "https://evil.example"}
    )
    check(code == 403, "web login from a foreign origin is refused (CSRF)")
    form = urllib.parse.urlencode({"email": env("GRADEMIND_ADMIN_EMAIL"), "password": env("GRADEMIND_ADMIN_PASSWORD")}).encode()
    code, h, _ = web("/api/session", form, {"content-type": "application/x-www-form-urlencoded", "origin": WEB})
    cookie = h.get("set-cookie", "")
    loc = h.get("location", "")
    check(loc == "/" or loc.startswith(WEB + "/"), f"web login redirects within the web origin (got {loc!r})")
    check(
        code == 303 and cookie.startswith("gm_session=") and "httponly" in cookie.lower(),
        "web login sets an httpOnly session cookie",
    )
    jar = {"cookie": cookie.split(";", 1)[0]}
    code, _, html = web("/", headers=jar)
    check(code == 200 and "Welcome, Administrator" in html and "System status" in html, "web dashboard renders for the admin")
    code, _, html = web("/exams", headers=jar)
    check(code == 200 and exam_name in html, "web exam list shows the exam created through the API")
    check("eyJ" not in html, "the access token does not appear in the page")


def main() -> int:
    for p in ["/health", "/health/db", "/health/redis", "/health/storage", "/health/models"] + (
        ["/health/ocr"] if REQUIRE_OCR else []
    ):
        code, body = call("GET", p)
        check(code == 200, f"{p} -> {code} {json.dumps(body)[:160]}")
    code, tok = call(
        "POST",
        "/api/auth/login",
        json.dumps({"email": env("GRADEMIND_ADMIN_EMAIL"), "password": env("GRADEMIND_ADMIN_PASSWORD")}).encode(),
        {"content-type": "application/json"},
    )
    check(code == 200, "admin login")
    auth = {"authorization": f"Bearer {tok['access_token']}"}
    code, exam = call(
        "POST",
        "/api/exams",
        json.dumps({"name": "Smoke", "subject": "Smoke", "total_marks": "10"}).encode(),
        {**auth, "content-type": "application/json"},
    )
    check(code == 201, "create exam")
    pdf = make_pdf([["SMOKE booklet " + uuid.uuid4().hex], ["page 2"]])
    b = uuid.uuid4().hex
    body = (
        (
            f'--{b}\r\nContent-Disposition: form-data; name="student_ref"\r\n\r\nSMOKE-1\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="smoke.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
        ).encode()
        + pdf
        + f"\r\n--{b}--\r\n".encode()
    )
    code, sub = call(
        "POST", f"/api/exams/{exam['id']}/submissions", body, {**auth, "content-type": f"multipart/form-data; boundary={b}"}
    )
    check(code == 201 and "job_id" in sub, "upload submission")

    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + 300  # the real OCR engine reads ~12 s per page on CPU
    req = urllib.request.Request(f"{API}/api/jobs/{sub['job_id']}/events", headers=auth)
    with urllib.request.urlopen(req, timeout=320) as r:
        ev: dict[str, str] = {}
        for raw in r:
            line = raw.decode().rstrip("\r\n")
            if not line:
                if ev.get("event") == "job":
                    events.append(json.loads(ev["data"]))
                    if events[-1]["status"] in ("COMPLETED", "FAILED", "REVIEW_REQUIRED"):
                        break
                ev = {}
            elif not line.startswith(":"):
                k, _, v = line.partition(":")
                ev[k] = v.strip()
            if time.monotonic() > deadline:
                break
    check(bool(events) and events[-1]["status"] == "COMPLETED", f"job via SSE -> {[e['status'] for e in events]}")
    code, job = call("GET", f"/api/jobs/{sub['job_id']}", headers=auth)
    stages = [(s["stage"], s["status"]) for s in job["stages"]]
    expected = [("INTAKE", "STARTED"), ("INTAKE", "SUCCEEDED"), ("RASTERIZE", "STARTED"), ("RASTERIZE", "SUCCEEDED")]
    check(stages == expected, f"the ingest job renders pages and nothing else (machine reading is its own job): {stages}")
    # 4.0: the machine reading is a separate job, run by the ocr-worker on the ocr queue
    mine: list[dict[str, Any]] = []
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        code, summary = call("GET", f"/api/exams/{exam['id']}/ocr-summary", headers=auth)
        mine = [r for r in summary if r["submission_id"] == sub["id"]]
        if mine and mine[0]["pages_read"] + mine[0]["pages_failed"] >= 2:
            break
        time.sleep(3)
    ok = bool(mine) and (mine[0]["pages"], mine[0]["pages_read"], mine[0]["pages_failed"]) == (2, 2, 0)
    check(ok, f"both pages machine-read by the real engine: {mine}")
    code, rows = call("GET", f"/api/exams/{exam['id']}/submissions", headers=auth)
    row = next(r for r in rows if r["id"] == sub["id"])
    check(
        row["job_kind"] == "ocr" and row["job_status"] == "COMPLETED",
        f"the latest job is the machine reading: {row['job_kind']} {row['job_status']}",
    )
    code, pages = call("GET", f"/api/submissions/{sub['id']}/pages", headers=auth)
    check(code == 200 and [pg["page_no"] for pg in pages] == [1, 2], "booklet rendered to 2 page images")
    with urllib.request.urlopen(pages[0]["image_url"], timeout=10) as r:
        check(r.read()[:3] == b"\xff\xd8\xff", "page image served via signed URL")
    code, detail = call("GET", f"/api/submissions/{sub['id']}", headers=auth)
    with urllib.request.urlopen(detail["source_url"], timeout=10) as r:
        check(r.read() == pdf, "signed URL serves the stored bytes")
    web_checks(exam_name="Smoke")
    header_checks()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
