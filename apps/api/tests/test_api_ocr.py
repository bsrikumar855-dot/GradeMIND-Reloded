"""Machine-reading API (3.1, D28): grading readiness does not depend on OCR; summary; re-read; RBAC; audit."""

from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from drive import drive
from fake_ocr import RESOLVED, FakeOcr, services
from pdfgen import make_pdf
from sqlalchemy import select
from sqlalchemy import text as sql
from sqlalchemy.exc import DBAPIError

from grademind_core.db.models import AuditLog, Page
from grademind_core.db.ocr_models import LineCorrection
from grademind_core.jobs import RetryPolicy
from grademind_core.line_corrections import apply_ops
from grademind_worker.stages import PIPELINES

pytestmark = [needs_db, needs_s3]


@pytest.fixture(scope="module")
def exam(world: dict[str, Any]) -> str:
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "OCR", "subject": "S", "total_marks": "5"}, headers=h).json()["id"]
    c.post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    return eid


def upload(w: dict[str, Any], exam_id: str, tag: str) -> dict[str, Any]:
    r = w["client"].post(
        f"/api/exams/{exam_id}/submissions",
        files={"file": ("b.pdf", make_pdf([[f"{tag} {uuid.uuid4().hex}"], ["page two"]]), "application/pdf")},
        data={"student_ref": tag},
        headers=login(w, "teacher"),
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def work(w: dict[str, Any], job_id: str, fake: FakeOcr | None = None, **kw: Any) -> Any:
    """Ingest the booklet, then read it (the machine-reading job the worker creates afterwards). Returns the last job's status."""
    status, _ran = drive(
        w["sessions"], uuid.UUID(job_id), PIPELINES, services(w["store"], fake or FakeOcr(), **kw), RetryPolicy(max_retries=0)
    )  # no automatic retry here: these tests look at the state a failure leaves behind (4.0 tests cover the retries)
    return status


def row(w: dict[str, Any], exam_id: str, sid: str, who: str = "teacher") -> dict[str, Any]:
    rows = w["client"].get(f"/api/exams/{exam_id}/submissions", headers=login(w, who)).json()
    (r,) = [x for x in rows if x["id"] == sid]
    out: dict[str, Any] = r
    return out


def test_grading_is_ready_when_pages_are_rendered_even_if_ocr_fails(world: dict[str, Any], exam: str) -> None:
    """The hard D28 rule: a dead OCR service makes the job FAILED, but the booklet can be graded."""
    up = upload(world, exam, "RDY-1")
    assert not row(world, exam, up["id"])["pages_ready"]  # nothing has run yet
    assert work(world, up["job_id"], FakeOcr(health_down=True)).value == "FAILED"
    r = row(world, exam, up["id"])
    assert r["job_status"] == "FAILED" and r["job_stage"] == "OCR" and r["page_count"] == 2 and r["pages_ready"] is True
    assert r["job_error"].startswith("OCR_FAILED:") and "Grading is not affected" in r["job_error"]


def test_summary_counts_read_and_failed_pages(world: dict[str, Any], exam: str) -> None:
    up = upload(world, exam, "SUM-1")
    assert work(world, up["job_id"], FakeOcr(fail_calls={2})).value == "COMPLETED"
    rows = world["client"].get(f"/api/exams/{exam}/ocr-summary", headers=login(world, "exA")).json()
    (mine,) = [r for r in rows if r["submission_id"] == up["id"]]
    assert (mine["pages"], mine["pages_read"], mine["pages_failed"]) == (2, 1, 1)
    for who in ("exB", "admin2"):
        assert world["client"].get(f"/api/exams/{exam}/ocr-summary", headers=login(world, who)).status_code == 404


def test_reread_rereads_only_the_failed_pages(world: dict[str, Any], exam: str) -> None:
    c = world["client"]
    up = upload(world, exam, "RR-1")
    work(world, up["job_id"], FakeOcr(fail_calls={1}))
    r = c.post(f"/api/submissions/{up['id']}/ocr/retry", headers=login(world, "exA"))  # an examiner may ask for it
    assert r.status_code == 202, r.text
    job = r.json()["job_id"]
    assert uuid.UUID(job) in world["queue"].sent
    assert (
        c.post(f"/api/submissions/{up['id']}/ocr/retry", headers=login(world, "exA")).json()["error"]["code"] == "already_running"
    )
    fake = FakeOcr()
    assert work(world, job, fake).value == "COMPLETED" and fake.calls == 1  # the one failed page
    rows = c.get(f"/api/exams/{exam}/ocr-summary", headers=login(world, "exA")).json()
    (mine,) = [x for x in rows if x["submission_id"] == up["id"]]
    assert (mine["pages_read"], mine["pages_failed"]) == (2, 0)
    with world["sessions"]() as s:
        assert "ocr.retry_requested" in set(s.scalars(select(AuditLog.action).where(AuditLog.entity_id == up["id"])))


def test_reread_is_scoped_and_authenticated(world: dict[str, Any], exam: str) -> None:
    up = upload(world, exam, "RR-2")
    work(world, up["job_id"])
    c = world["client"]
    for who in ("exB", "admin2", "ex2"):
        assert c.post(f"/api/submissions/{up['id']}/ocr/retry", headers=login(world, who)).status_code == 404
    assert c.post(f"/api/submissions/{up['id']}/ocr/retry").status_code == 401
    assert c.post(f"/api/submissions/{uuid.uuid4()}/ocr/retry", headers=login(world, "admin")).status_code == 404


# --------------------------------------------------------------------------------------- 3.2: machine reading per region


def lv(i: str, m: str, d: str = "") -> dict[str, str]:
    return {"id": i, "name": i, "marks": m, "definition": d}


@pytest.fixture(scope="module")
def graded_exam(world: dict[str, Any]) -> str:
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "MR", "subject": "S", "total_marks": "5"}, headers=h).json()["id"]
    c.post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    paper = {
        "total_marks": "5",
        "questions": [{"id": "q1", "label": "1", "max_marks": "2"}, {"id": "q2", "label": "2", "max_marks": "3"}],
    }
    assert c.put(f"/api/exams/{eid}/paper/draft", json={"document": paper}, headers=h).status_code == 200
    assert c.post(f"/api/exams/{eid}/paper/approve", headers=h).status_code == 200
    rubric = {
        "questions": [
            {"qid": "q1", "criteria": [{"id": "c1", "name": "D", "levels": [lv("none", "0"), lv("full", "2")]}]},
            {"qid": "q2", "criteria": [{"id": "c1", "name": "C", "levels": [lv("none", "0"), lv("full", "3")]}]},
        ]
    }
    assert c.put(f"/api/exams/{eid}/rubric/draft", json={"document": rubric}, headers=h).status_code == 200
    assert c.post(f"/api/exams/{eid}/rubric/approve", headers=h).status_code == 200
    return eid


def pages_of(w: dict[str, Any], sid: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = w["client"].get(f"/api/submissions/{sid}/pages", headers=login(w, "exA")).json()
    return out


def draw(w: dict[str, Any], sid: str, page_id: str, qid: str, bbox: list[float], who: str = "exA") -> dict[str, Any]:
    r = w["client"].post(
        f"/api/submissions/{sid}/regions", json={"page_id": page_id, "bbox": bbox, "qid": qid}, headers=login(w, who)
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def reading(w: dict[str, Any], sid: str, who: str = "exA") -> dict[str, Any]:
    r = w["client"].get(f"/api/submissions/{sid}/machine-reading", headers=login(w, who))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def test_lines_inside_a_region_come_back_in_reading_order_with_a_may_be_wrong_notice(
    world: dict[str, Any], graded_exam: str
) -> None:
    up = upload(world, graded_exam, "MR-1")
    work(world, up["job_id"])
    p1, p2 = pages_of(world, up["id"])
    draw(world, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.15])  # covers the first fake line only
    draw(world, up["id"], p1["id"], "q2", [0.05, 0.05, 0.95, 0.30])  # covers both lines
    draw(world, up["id"], p2["id"], "q2", [0.05, 0.05, 0.95, 0.30], who="admin")
    mr = reading(world, up["id"])
    assert "can be wrong" in mr["notice"] and mr["low_confidence_below"] == 0.8
    only_first, both, other_page = mr["regions"]
    assert [ln["text"] for ln in only_first["lines"]] == ["page 1 first line"] and only_first["page_status"] == "read"
    assert [ln["text"] for ln in both["lines"]] == ["page 1 first line", "page 1 second line"]
    first, second = both["lines"]
    assert (first["score"], first["low_confidence"], first["overlap"]) == (0.93, False, 1.0)
    assert (second["score"], second["low_confidence"]) == (0.41, True)  # flagged for a closer look
    # the same fractional space as the answer regions (the stored box is in whole pixels, hence the 1-pixel tolerance)
    assert first["bbox"] == pytest.approx([0.1, 0.1, 0.9, 0.13], abs=1 / 1240)
    assert [ln["text"] for ln in other_page["lines"]] == ["page 2 first line", "page 2 second line"] and other_page[
        "page_no"
    ] == 2


def test_pages_without_machine_text_are_reported_not_hidden(world: dict[str, Any], graded_exam: str) -> None:
    c = world["client"]
    up = upload(world, graded_exam, "MR-2")
    # rendered but never machine-read (the OCR stage has not run: the service is down)
    assert work(world, up["job_id"], FakeOcr(health_down=True)).value == "FAILED"
    p1, p2 = pages_of(world, up["id"])
    draw(world, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.30])
    draw(world, up["id"], p2["id"], "q2", [0.05, 0.05, 0.95, 0.30])
    first, second = reading(world, up["id"])["regions"]
    assert (first["page_status"], first["lines"]) == ("unread", []) and (second["page_status"], second["lines"]) == ("unread", [])
    # page 1 fails to read, page 2 reads
    job = c.post(f"/api/submissions/{up['id']}/ocr/retry", headers=login(world, "exA")).json()["job_id"]
    assert work(world, job, FakeOcr(fail_calls={1})).value == "COMPLETED"
    first, second = reading(world, up["id"])["regions"]
    assert (first["page_status"], first["lines"]) == ("failed", [])
    assert second["page_status"] == "read" and len(second["lines"]) == 2  # the other page is unaffected


def test_deleted_regions_drop_out_and_crossed_out_ones_say_so(world: dict[str, Any], graded_exam: str) -> None:
    c = world["client"]
    up = upload(world, graded_exam, "MR-3")
    work(world, up["job_id"])
    p1, _ = pages_of(world, up["id"])
    keep = draw(world, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.30])
    gone = draw(world, up["id"], p1["id"], "q2", [0.05, 0.05, 0.95, 0.30])
    assert c.delete(f"/api/submissions/{up['id']}/regions/{gone['id']}", headers=login(world, "exA")).status_code == 204
    c.post(f"/api/submissions/{up['id']}/regions/{keep['id']}/crossed", json={"crossed_out": True}, headers=login(world, "exA"))
    (only,) = reading(world, up["id"])["regions"]
    assert only["region_id"] == keep["id"] and only["crossed_out"] is True


def test_machine_reading_is_scoped_like_the_booklet(world: dict[str, Any], graded_exam: str) -> None:
    up = upload(world, graded_exam, "MR-4")
    work(world, up["job_id"])
    c = world["client"]
    for who in ("exB", "admin2", "ex2"):
        assert c.get(f"/api/submissions/{up['id']}/machine-reading", headers=login(world, who)).status_code == 404
    assert c.get(f"/api/submissions/{up['id']}/machine-reading").status_code == 401
    assert c.get(f"/api/submissions/{uuid.uuid4()}/machine-reading", headers=login(world, "admin")).status_code == 404


def test_grading_is_identical_with_and_without_machine_reading(world: dict[str, Any], graded_exam: str) -> None:
    """D28 from the data side: the same booklet, regions and verdicts score the same whether or not it was ever machine-read,
    and no machine text appears anywhere in the grading workspace."""
    c = world["client"]

    def graded(tag: str, ocr: bool) -> tuple[dict[str, Any], str]:
        up = upload(world, graded_exam, tag)
        work(world, up["job_id"], FakeOcr() if ocr else FakeOcr(health_down=True))
        p1, p2 = pages_of(world, up["id"])
        draw(world, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.30])
        draw(world, up["id"], p2["id"], "q2", [0.05, 0.05, 0.95, 0.30])
        for qid, level in (("q1", "full"), ("q2", "none")):
            r = c.put(
                f"/api/submissions/{up['id']}/evaluations/{qid}/1", json={"verdicts": {"c1": level}}, headers=login(world, "exA")
            )
            assert r.status_code == 200, r.text
        ws = c.get(f"/api/submissions/{up['id']}/workspace", headers=login(world, "exA"))
        return ws.json(), ws.text

    with_ocr, with_text = graded("EQ-1", ocr=True)
    without_ocr, without_text = graded("EQ-2", ocr=False)
    assert with_ocr["score"] == without_ocr["score"] and with_ocr["score"]["total"] == "2"
    assert [e["marks"] for e in with_ocr["evaluations"]] == [e["marks"] for e in without_ocr["evaluations"]]
    assert "first line" not in with_text and "second line" not in with_text  # the workspace carries no machine text at all


# ----------------------------------------------------------------------------------------- 3.4: examiner line correction


def booklet_with_lines(w: dict[str, Any], exam_id: str, tag: str) -> tuple[str, list[dict[str, Any]]]:
    up = upload(w, exam_id, tag)
    work(w, up["job_id"])
    p1, _ = pages_of(w, up["id"])
    draw(w, up["id"], p1["id"], "q1", [0.05, 0.05, 0.95, 0.30])
    (region,) = reading(w, up["id"])["regions"]
    return up["id"], region["lines"]


def correct(w: dict[str, Any], sid: str, line_id: str, text: str, expected: str | None = None, who: str = "exA") -> Any:
    return w["client"].put(
        f"/api/submissions/{sid}/ocr-lines/{line_id}/correction",
        json={"text": text, "expected_correction_id": expected},
        headers=login(w, who),
    )


def test_a_correction_is_stored_as_labelled_data_with_crop_and_provenance(world: dict[str, Any], graded_exam: str) -> None:
    sid, lines = booklet_with_lines(world, graded_exam, "LC-1")
    low = lines[1]  # the 0.41 line, flagged for a closer look
    assert low["low_confidence"] is True and low["corrected"] is False and low["correction_id"] is None
    page_before = world["store"].get_bytes(_page_key(world, sid))

    r = correct(world, sid, low["id"], "page 1 second line, corrected [?]")
    assert r.status_code == 200, r.text
    assert r.json()["corrected"] is True and r.json()["text"] == "page 1 second line, corrected [?]"
    assert r.json()["original_text"] == "page 1 second line"

    # the machine-reading payload now shows the correction, keeps the original, and no longer flags the line
    again = reading(world, sid)["regions"][0]["lines"][1]
    assert (again["text"], again["original_text"], again["corrected"], again["low_confidence"]) == (
        "page 1 second line, corrected [?]",
        "page 1 second line",
        True,
        False,
    )
    assert again["correction_id"] == r.json()["correction_id"]

    with world["sessions"]() as s:
        row = s.get(LineCorrection, uuid.UUID(r.json()["correction_id"]))
        assert row is not None
        page = s.get(Page, row.page_id)
        assert page is not None
    assert (row.ocr_text, row.corrected_text, row.supersedes_id) == (
        "page 1 second line",
        "page 1 second line, corrected [?]",
        None,
    )
    assert apply_ops(row.ocr_text, row.edit_ops) == row.corrected_text  # the operations replay to the correction
    assert row.ocr_line_id == uuid.UUID(low["id"]) and row.subject == "S" and row.consent_scope.value == "local_only"
    assert row.examiner_id == world["exA"] and row.page_image_sha256 == page.sha256 and row.preprocessing_version == "none"
    assert (row.ocr_provider, row.ocr_model_names) == ("paddle_v6", {"det": "PP-OCRv6_medium_det", "rec": "PP-OCRv6_medium_rec"})
    assert row.ocr_weights_sha256 == {"det": RESOLVED["models"]["det"]["sha256"], "rec": RESOLVED["models"]["rec"]["sha256"]}
    # the crop is a real stored image whose hash matches, inside the page, and the polygon is kept for re-cropping later
    crop = world["store"].get_bytes(row.crop_object_key)
    assert crop[:3] == b"\xff\xd8\xff" and hashlib.sha256(crop).hexdigest() == row.crop_sha256
    x0, y0, x1, y1 = row.crop_bbox
    assert 0 <= x0 < x1 <= page.width and 0 <= y0 < y1 <= page.height and row.crop_polygon
    # the page image was only READ: byte-identical afterwards (D19)
    assert world["store"].get_bytes(page.object_key) == page_before


def _page_key(w: dict[str, Any], sid: str) -> str:
    with w["sessions"]() as s:
        return s.scalars(select(Page).where(Page.submission_id == uuid.UUID(sid)).order_by(Page.page_no)).first().object_key  # type: ignore[union-attr]


def test_corrections_chain_and_stale_edits_are_refused(world: dict[str, Any], graded_exam: str) -> None:
    sid, lines = booklet_with_lines(world, graded_exam, "LC-2")
    line = lines[0]["id"]
    first = correct(world, sid, line, "first fix").json()
    assert (
        correct(world, sid, line, "again", expected=None).json()["error"]["code"] == "line_changed"
    )  # saw nothing, but there is one
    assert correct(world, sid, line, "again", expected=str(uuid.uuid4())).status_code == 409
    second = correct(world, sid, line, "second fix", expected=first["correction_id"], who="teacher")
    assert second.status_code == 200
    # the second one keeps the ORIGINAL machine text, and supersedes the first
    with world["sessions"]() as s:
        rows = s.scalars(select(LineCorrection).where(LineCorrection.ocr_line_id == uuid.UUID(line))).all()
    by_id = {str(r.id): r for r in rows}
    assert by_id[second.json()["correction_id"]].supersedes_id == uuid.UUID(first["correction_id"])
    assert (
        by_id[second.json()["correction_id"]].ocr_text == "page 1 first line"
        and by_id[first["correction_id"]].corrected_text == "first fix"
    )
    hist = world["client"].get(f"/api/submissions/{sid}/ocr-lines/{line}/corrections", headers=login(world, "exA")).json()
    assert [c["corrected_text"] for c in hist["corrections"]] == ["first fix", "second fix"] and hist[
        "original_text"
    ] == "page 1 first line"
    assert hist["corrections"][1]["examiner_id"] == str(world["teacher"])
    # going back to the original text is a valid (new) correction; repeating the current text is not
    assert correct(world, sid, line, "page 1 first line", expected=second.json()["correction_id"]).status_code == 200
    cur = reading(world, sid)["regions"][0]["lines"][0]["correction_id"]
    assert correct(world, sid, line, "page 1 first line", expected=cur).json()["error"]["code"] == "no_change"


def test_invalid_text_is_refused_and_nothing_is_stored(world: dict[str, Any], graded_exam: str) -> None:
    sid, lines = booklet_with_lines(world, graded_exam, "LC-3")
    line = lines[0]["id"]
    for bad, code in [
        ("one\ntwo", "invalid_characters"),
        ("pay\u202etxt", "invalid_characters"),
        ("a\u200bb", "invalid_characters"),
        ("x" * 2001, "too_long"),
    ]:
        r = correct(world, sid, line, bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (bad[:10], r.text)
    assert correct(world, sid, line, "x" * 4001).status_code == 422  # over the schema limit as well
    with world["sessions"]() as s:
        assert s.scalars(select(LineCorrection).where(LineCorrection.ocr_line_id == uuid.UUID(line))).all() == []
    assert correct(world, sid, line, "").status_code == 200  # an empty correction is valid: the machine invented the line


def test_two_examiners_correcting_the_same_line_at_once_cannot_both_win(world: dict[str, Any], graded_exam: str) -> None:
    sid, lines = booklet_with_lines(world, graded_exam, "LC-4")
    line = lines[0]["id"]
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda who_text: correct(world, sid, line, who_text[1], who=who_text[0]).status_code,
                [("exA", "alpha"), ("teacher", "beta")],
            )
        )
    assert sorted(results) == [200, 409]
    with world["sessions"]() as s:
        assert len(s.scalars(select(LineCorrection).where(LineCorrection.ocr_line_id == uuid.UUID(line))).all()) == 1


def test_the_audit_row_is_written_with_the_correction_and_holds_no_student_text(world: dict[str, Any], graded_exam: str) -> None:
    sid, lines = booklet_with_lines(world, graded_exam, "LC-5")
    r = correct(world, sid, lines[0]["id"], "SECRET-STUDENT-WORDS fix")
    cid = r.json()["correction_id"]
    with world["sessions"]() as s:
        audit = s.scalars(select(AuditLog).where(AuditLog.entity_id == cid)).one()
    assert audit.action == "line.correct" and audit.actor_id == world["exA"] and audit.request_id == r.headers["x-request-id"]
    assert audit.details["ocr_line_id"] == lines[0]["id"] and audit.details["supersedes_id"] is None and audit.details["ops"] >= 1
    assert "SECRET-STUDENT-WORDS" not in json.dumps(audit.details) and "first line" not in json.dumps(audit.details)


def test_corrections_are_append_only_and_scoped(world: dict[str, Any], graded_exam: str) -> None:
    sid, lines = booklet_with_lines(world, graded_exam, "LC-6")
    other_sid, other_lines = booklet_with_lines(world, graded_exam, "LC-6b")
    line = lines[0]["id"]
    assert correct(world, sid, line, "kept").status_code == 200
    for stmt in ("UPDATE line_corrections SET corrected_text = 'x'", "DELETE FROM line_corrections"):
        with pytest.raises(DBAPIError, match="append-only"), world["sessions"]() as s, s.begin():
            s.execute(sql(stmt))
    for who in ("exB", "admin2", "ex2"):
        assert correct(world, sid, line, "no", who=who).status_code == 404
        assert (
            world["client"].get(f"/api/submissions/{sid}/ocr-lines/{line}/corrections", headers=login(world, who)).status_code
            == 404
        )
    assert world["client"].put(f"/api/submissions/{sid}/ocr-lines/{line}/correction", json={"text": "x"}).status_code == 401
    assert correct(world, sid, other_lines[0]["id"], "wrong booklet").status_code == 404  # a line of ANOTHER booklet
    assert correct(world, sid, str(uuid.uuid4()), "no such line").status_code == 404
    assert other_sid


def test_corrections_never_change_grading(world: dict[str, Any], graded_exam: str) -> None:
    c = world["client"]
    sid, lines = booklet_with_lines(world, graded_exam, "LC-7")

    def snapshot() -> tuple[Any, str]:
        r = c.get(f"/api/submissions/{sid}/workspace", headers=login(world, "exA"))
        return r.json()["score"], r.text

    assert (
        c.put(
            f"/api/submissions/{sid}/evaluations/q1/1", json={"verdicts": {"c1": "full"}}, headers=login(world, "exA")
        ).status_code
        == 200
    )
    before, _ = snapshot()
    assert correct(world, sid, lines[0]["id"], "CORRECTED-TEXT-XYZ").status_code == 200
    after, raw = snapshot()
    assert before == after and before["total"] == "2"
    assert "CORRECTED-TEXT-XYZ" not in raw  # the workspace still carries no machine or corrected text


def test_no_http_route_exports_the_dataset(world: dict[str, Any]) -> None:
    """3.5: the labelled-dataset export is a command-line, admin-gated, audited operation. The web app must not expose it."""
    paths = set(world["client"].get("/openapi.json").json()["paths"])  # the contract clients actually see
    assert not [p for p in paths if "export" in p.lower().replace("totals.csv", "") or "dataset" in p.lower()], sorted(paths)
    # the only routes that return line_corrections content are the per-line correction/history of a visible booklet
    assert "/api/submissions/{submission_id}/ocr-lines/{line_id}/corrections" in paths
