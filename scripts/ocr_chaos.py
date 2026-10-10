"""CI evidence for step 4.0 (D29): kill the OCR service in the MIDDLE of a booklet, bring it back, and show that nobody had to
click anything: the reading is retried by itself with backoff, finished pages are not read twice, grading stayed possible.

Run against a live `docker compose` stack (needs the docker CLI). Exit code 0 = pass. Prints the evidence it checks.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from typing import Any

from compose_smoke import call, check, env
from pdfgen import make_pdf

BOOKLET_PAGES = 4
PSQL = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "grademind", "-d", "grademind", "-At", "-c"]


def sh(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, text=True)  # noqa: S603 - fixed argv


def sql(q: str) -> str:
    return subprocess.run([*PSQL, q], check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603


def wait(what: str, fn: Any, timeout: float, every: float = 2.0) -> Any:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    check(False, f"timed out waiting for: {what}")


def main() -> int:
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
        json.dumps({"name": "Chaos", "subject": "Chaos", "total_marks": "10"}).encode(),
        {**auth, "content-type": "application/json"},
    )
    check(code == 201, "create exam")
    b = uuid.uuid4().hex
    pdf = make_pdf([[f"CHAOS booklet {uuid.uuid4().hex} page {n}"] for n in range(1, BOOKLET_PAGES + 1)])
    body = (
        (
            f'--{b}\r\nContent-Disposition: form-data; name="student_ref"\r\n\r\nCHAOS-1\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="c.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
        ).encode()
        + pdf
        + f"\r\n--{b}--\r\n".encode()
    )
    code, sub = call(
        "POST", f"/api/exams/{exam['id']}/submissions", body, {**auth, "content-type": f"multipart/form-data; boundary={b}"}
    )
    check(code == 201, f"upload a {BOOKLET_PAGES}-page booklet")
    sid, eid = str(uuid.UUID(sub["id"])), exam["id"]  # a validated UUID: safe to put in the psql queries below

    def row() -> dict[str, Any]:
        _, rows = call("GET", f"/api/exams/{eid}/submissions", headers=auth)
        return next(r for r in rows if r["id"] == sid)  # type: ignore[no-any-return]

    def summary() -> dict[str, Any]:
        _, s = call("GET", f"/api/exams/{eid}/ocr-summary", headers=auth)
        return next((r for r in s if r["submission_id"] == sid), {"pages": 0, "pages_read": 0, "pages_failed": 0})

    wait("pages rendered", lambda: row()["pages_ready"], 120)
    check(True, "pages rendered: grading is possible")
    wait("the first page read", lambda: summary()["pages_read"] >= 1, 240, every=1)
    before = summary()
    check(0 < before["pages_read"] < BOOKLET_PAGES, f"mid-booklet: {before['pages_read']} of {BOOKLET_PAGES} pages read")

    sh("docker", "compose", "kill", "ocr")  # SIGKILL: the service dies without finishing its page
    print("KILLED the OCR service", flush=True)

    r = wait("an automatic retry to be scheduled", lambda: row() if row()["job_retry_count"] >= 1 else None, 180)
    check(r["pages_ready"] is True, "grading stayed possible while the service was down")
    check(
        r["job_status"] in ("QUEUED", "RUNNING", "FAILED") and r["job_kind"] == "ocr",
        f"the reading job was retried by itself: kind={r['job_kind']} status={r['job_status']} "
        f"retry_count={r['job_retry_count']}",
    )
    down = summary()
    check(down["pages_read"] < BOOKLET_PAGES, f"not finished while the service is down: {down['pages_read']} of {BOOKLET_PAGES}")

    sh("docker", "compose", "start", "ocr")
    print("RESTARTED the OCR service", flush=True)

    done = wait(
        "the booklet to be fully read", lambda: summary() if summary()["pages_read"] >= BOOKLET_PAGES else None, 420, every=3
    )
    check(
        (done["pages"], done["pages_read"], done["pages_failed"]) == (BOOKLET_PAGES, BOOKLET_PAGES, 0),
        f"after the restart every page was read, with no manual retry: {done}",
    )
    q_twice = (  # sid is a validated UUID
        "select count(*) from (select r.page_id, count(*) c from ocr_runs r join pages p on p.id = r.page_id "  # noqa: S608
        f"where p.submission_id = '{sid}' and r.status = 'OK' group by r.page_id) t where c <> 1"
    )
    ok_per_page = sql(q_twice)
    check(ok_per_page == "0", "no page was read twice (exactly one OK run per page)")
    final = row()
    print(
        f"EVIDENCE job_kind={final['job_kind']} job_status={final['job_status']} retry_count={final['job_retry_count']} "
        f"pages_read={done['pages_read']}/{BOOKLET_PAGES}",
        flush=True,
    )
    print(
        sql(f"select kind, status, retry_count from processing_jobs where submission_id = '{sid}' order by created_at"),  # noqa: S608
        flush=True,
    )
    print("OCR CHAOS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
