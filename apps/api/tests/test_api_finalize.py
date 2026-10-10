# ruff: noqa: E501 - long SQL strings in the database-guard tests
"""4.2 (D29): finalize, reopen and verify. Real Postgres + MinIO + the real ingest pipeline (in-process).

What must hold: only an administrator or teacher can finalize or reopen; nothing is finalizable until every mapped answer is fully
graded (not-attempted questions need a person's confirmation); a finalized result is read-only through the API AND the database;
every finalize freezes an append-only snapshot; a reopen needs a reason and is audited and the next finalize adds snapshot n+1;
and the verify command recomputes every snapshot from the raw records and fails loudly on any difference (I12)."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from conftest import login, needs_db, needs_s3
from fake_ocr import services
from pdfgen import make_pdf
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from grademind_core.cli import main as cli_main
from grademind_core.db.models import AuditLog, Evaluation, FinalizationEvent, ResultSnapshot
from grademind_core.jobs import run_job
from grademind_core.result_snapshots import verify_all
from grademind_worker.stages import PIPELINES

pytestmark = [needs_db, needs_s3]


def lv(i: str, m: str, d: str = "") -> dict[str, str]:
    return {"id": i, "name": i, "marks": m, "definition": d}


PAPER = {
    "total_marks": "5",
    "questions": [
        {"id": "q1", "label": "1", "max_marks": "2"},
        {
            "id": "q2",
            "label": "2",
            "choose": 1,
            "children": [{"id": "q2a", "label": "(a)", "max_marks": "3"}, {"id": "q2b", "label": "(b)", "max_marks": "3"}],
        },
    ],
}
RUBRIC = {
    "questions": [
        {
            "qid": "q1",
            "criteria": [
                {"id": "c1", "name": "Definition", "levels": [lv("none", "0"), lv("part", "1", "half"), lv("full", "2")]}
            ],
        },
        {"qid": "q2a", "criteria": [{"id": "c1", "name": "A", "levels": [lv("none", "0"), lv("full", "3")]}]},
        {
            "qid": "q2b",
            "criteria": [
                {"id": "m", "name": "Method", "levels": [lv("none", "0"), lv("full", "2")]},
                {"id": "u", "name": "Units", "levels": [lv("none", "0"), lv("ok", "1")]},
            ],
        },
    ]
}


def setup_exam(world: dict[str, Any]) -> dict[str, Any]:
    """An exam with the approved paper and rubric above, the examiner assigned (shared with the report tests)."""
    c, h = world["client"], login(world, "teacher")
    eid = c.post("/api/exams", json={"name": "Finalize", "subject": "EVS", "total_marks": "5"}, headers=h).json()["id"]
    c.post(f"/api/exams/{eid}/assignments", json={"user_id": str(world["exA"])}, headers=login(world, "admin"))
    c.put(f"/api/exams/{eid}/paper/draft", json={"document": PAPER}, headers=h)
    assert c.post(f"/api/exams/{eid}/paper/approve", headers=h).status_code == 200
    assert c.put(f"/api/exams/{eid}/rubric/draft", json={"document": RUBRIC}, headers=h).json()["issues"] == []
    assert c.post(f"/api/exams/{eid}/rubric/approve", headers=h).status_code == 200
    return {"exam": eid}


@pytest.fixture(scope="module")
def ex(world: dict[str, Any]) -> dict[str, Any]:
    return setup_exam(world)


def booklet(world: dict[str, Any], ex: dict[str, Any], ref: str) -> dict[str, Any]:
    c, h = world["client"], login(world, "teacher")
    up = c.post(
        f"/api/exams/{ex['exam']}/submissions",
        files={"file": ("b.pdf", make_pdf([[f"{ref} " + uuid.uuid4().hex], ["page two"]]), "application/pdf")},
        data={"student_ref": ref},
        headers=h,
    ).json()
    assert str(run_job(world["sessions"], uuid.UUID(up["job_id"]), PIPELINES, services=services(world["store"]))) == "COMPLETED"
    ws = c.get(f"/api/submissions/{up['id']}/workspace", headers=h).json()
    return {"sub": up["id"], "pages": [pg["id"] for pg in ws["pages"]]}


def region(world: dict[str, Any], b: dict[str, Any], qid: str, who: str = "exA", **kw: Any) -> Any:
    body = {"page_id": b["pages"][0], "bbox": [0.1, 0.1, 0.9, 0.5], "qid": qid, **kw}
    return world["client"].post(f"/api/submissions/{b['sub']}/regions", json=body, headers=login(world, who))


def grade(world: dict[str, Any], b: dict[str, Any], qid: str, att: int, verdicts: dict[str, str], who: str = "exA") -> Any:
    return world["client"].put(
        f"/api/submissions/{b['sub']}/evaluations/{qid}/{att}", json={"verdicts": verdicts}, headers=login(world, who)
    )


def fin(world: dict[str, Any], b: dict[str, Any], who: str = "teacher") -> dict[str, Any]:
    r = world["client"].get(f"/api/submissions/{b['sub']}/finalization", headers=login(world, who))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def finalize(world: dict[str, Any], b: dict[str, Any], who: str = "teacher", confirm: bool = True) -> Any:
    return world["client"].post(
        f"/api/submissions/{b['sub']}/finalize", json={"confirm_not_attempted": confirm}, headers=login(world, who)
    )


def reopen(world: dict[str, Any], b: dict[str, Any], reason: str, who: str = "teacher") -> Any:
    return world["client"].post(f"/api/submissions/{b['sub']}/reopen", json={"reason": reason}, headers=login(world, who))


def graded(world: dict[str, Any], ex: dict[str, Any], ref: str, q1: str = "full") -> dict[str, Any]:
    """A booklet with Q1 mapped and graded (Q2 never attempted)."""
    b = booklet(world, ex, ref)
    assert region(world, b, "q1").status_code == 201
    assert grade(world, b, "q1", 1, {"c1": q1}).status_code == 200
    return b


def verified(world: dict[str, Any], **kw: Any) -> Any:
    with world["sessions"]() as s:
        return verify_all(s, **kw)


def audit_actions(world: dict[str, Any], sub: str) -> list[str]:
    with world["sessions"]() as s:
        rows = s.scalars(select(AuditLog).order_by(AuditLog.at))
        return [a.action for a in rows if (a.details or {}).get("submission_id") == sub and a.action.startswith("submission.")]


# -------------------------------------------------------------------------------------------------- readiness


def test_a_booklet_is_not_ready_until_every_mapped_answer_is_fully_graded(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = booklet(world, ex, "FR-1")
    s = fin(world, b)
    assert s["state"] == "OPEN" and s["ready"] is False and [x["code"] for x in s["blockers"]] == ["no_answers"]
    assert (
        world["client"].post(f"/api/submissions/{b['sub']}/finalize", json={}, headers=login(world, "teacher")).status_code == 409
    )

    region(world, b, "q2b")
    region(world, b, "q1")
    grade(world, b, "q1", 1, {"c1": "full"})
    grade(world, b, "q2b", 1, {"m": "full"})  # Units has no verdict yet
    s = fin(world, b)
    assert not s["ready"] and [x["code"] for x in s["blockers"]] == ["incomplete"]
    assert "(b)" in s["blockers"][0]["message"]
    r = finalize(world, b)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_ready"
    assert r.json()["error"]["issues"][0]["code"] == "incomplete"

    grade(world, b, "q2b", 1, {"m": "full", "u": "ok"})
    s = fin(world, b)
    assert s["ready"] is True and s["blockers"] == []
    assert [n["qid"] for n in s["not_attempted"]] == []  # Q2 is answered, so the unused alternative (a) is not "not attempted"


def test_questions_with_no_answer_box_need_a_confirmation(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "FR-2")
    s = fin(world, b)
    assert s["ready"] and {n["qid"] for n in s["not_attempted"]} == {"q2a", "q2b"}
    r = finalize(world, b, confirm=False)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_attempted_unconfirmed"
    assert world["client"].get(f"/api/submissions/{b['sub']}/snapshots", headers=login(world, "teacher")).json() == []
    assert finalize(world, b, confirm=True).status_code == 201


# -------------------------------------------------------------------------------------------------- finalize


def test_only_admin_or_teacher_finalizes_and_nothing_crosses_an_organisation(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "FN-1")
    assert finalize(world, b, who="exA").status_code == 403
    assert reopen(world, b, "a long enough reason", who="exA").status_code == 403
    assert finalize(world, b, who="admin2").status_code == 404
    r = finalize(world, b, who="admin")
    assert r.status_code == 201, r.text
    snap = r.json()
    assert snap["snapshot_no"] == 1 and snap["total"] == "2" and snap["max_total"] == "5" and snap["rubric_version_no"] == 1
    assert snap["score_computer_version"].startswith("score-computer-") and snap["finalized_by"] == "admin"
    assert snap["nodes"]["q1"]["marks"] == "2" and snap["nodes"]["q2a"]["status"] == "NOT_ATTEMPTED"
    assert audit_actions(world, b["sub"]) == ["submission.finalize"]
    assert finalize(world, b).status_code == 409  # already final
    assert finalize(world, b).json()["error"]["code"] == "already_finalized"
    # an examiner can SEE the state and the frozen result
    seen = fin(world, b, who="exA")
    assert seen["state"] == "FINALIZED" and seen["snapshot"]["total"] == "2"


def test_a_finalized_result_is_read_only_through_the_api(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "FN-2")
    rid = region(world, b, "q2a").json()["id"]
    assert grade(world, b, "q2a", 1, {"c1": "none"}).status_code == 200
    assert finalize(world, b).status_code == 201
    c = world["client"]
    ex_h = login(world, "exA")
    blocked = [
        region(world, b, "q2b"),
        c.delete(f"/api/submissions/{b['sub']}/regions/{rid}", headers=ex_h),
        c.post(f"/api/submissions/{b['sub']}/regions/{rid}/crossed", json={"crossed_out": True}, headers=ex_h),
        grade(world, b, "q1", 1, {"c1": "none"}),
        grade(world, b, "q1", 1, {"c1": "none"}, who="admin"),
    ]
    assert [r.status_code for r in blocked] == [409] * 5
    assert {r.json()["error"]["code"] for r in blocked} == {"finalized"}
    ws = c.get(f"/api/submissions/{b['sub']}/workspace", headers=ex_h).json()
    assert ws["score"]["total"] == "2" and ws["finalization"]["state"] == "FINALIZED"  # reading is always allowed


def test_the_database_itself_refuses_changes_to_a_finalized_result(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "FN-3")
    assert finalize(world, b).status_code == 201
    sid = uuid.UUID(b["sub"])
    stmts = [
        # (statement, params): none of these may succeed while the booklet is finalized, whoever runs them
        ("UPDATE answer_regions SET crossed_out = true WHERE submission_id = :s", {"s": sid}),
        ("UPDATE answer_regions SET deleted_at = now() WHERE submission_id = :s", {"s": sid}),
        ("DELETE FROM answer_regions WHERE submission_id = :s", {"s": sid}),
        (
            "INSERT INTO answer_regions (id, submission_id, page_id, bbox, qid, attempt_no, crossed_out, created_by) "
            "SELECT gen_random_uuid(), submission_id, page_id, bbox, 'q1', 9, false, created_by FROM answer_regions "
            "WHERE submission_id = :s LIMIT 1",
            {"s": sid},
        ),
        (
            "INSERT INTO evaluations (id, submission_id, rubric_version_id, qid, attempt_no, verdicts, notes, marks, examiner_id, "
            "is_override, score_computer_version) SELECT gen_random_uuid(), submission_id, rubric_version_id, qid, attempt_no, "
            "verdicts, notes, marks, examiner_id, false, score_computer_version FROM evaluations WHERE submission_id = :s LIMIT 1",
            {"s": sid},
        ),
        (
            "INSERT INTO score_results (id, submission_id, rubric_version_id, total, max_total, complete, sheet, flags, "
            "score_computer_version, created_by) SELECT gen_random_uuid(), submission_id, rubric_version_id, total, max_total, "
            "complete, sheet, flags, score_computer_version, created_by FROM score_results WHERE submission_id = :s LIMIT 1",
            {"s": sid},
        ),
        ("UPDATE result_snapshots SET total = 99 WHERE submission_id = :s", {"s": sid}),
        ("DELETE FROM result_snapshots WHERE submission_id = :s", {"s": sid}),
        ("UPDATE finalization_events SET reason = 'edited' WHERE submission_id = :s", {"s": sid}),
        ("DELETE FROM finalization_events WHERE submission_id = :s", {"s": sid}),
    ]
    for sql, params in stmts:
        with pytest.raises(DBAPIError, match="finalized|append-only"), world["sessions"]() as s, s.begin():
            s.execute(text(sql), params)


def test_finalization_events_must_alternate_and_a_reopen_needs_a_reason_in_the_database(
    world: dict[str, Any], ex: dict[str, Any]
) -> None:
    b = graded(world, ex, "FN-4")
    sid = uuid.UUID(b["sub"])
    snap_id = finalize(world, b).json()["id"]
    with world["sessions"]() as s, s.begin():  # a second FINALIZED in a row
        with pytest.raises(DBAPIError, match="already finalized"), s.begin_nested():
            s.add(
                FinalizationEvent(submission_id=sid, action="FINALIZED", snapshot_id=uuid.UUID(snap_id), actor_id=world["admin"])
            )
            s.flush()
        with pytest.raises(DBAPIError, match="reopen_reason|violates check"), s.begin_nested():  # REOPENED without a reason
            s.add(
                FinalizationEvent(submission_id=sid, action="REOPENED", snapshot_id=uuid.UUID(snap_id), actor_id=world["admin"])
            )
            s.flush()
    assert reopen(world, b, "needs a recheck of Q1").status_code == 200
    with pytest.raises(DBAPIError, match="not finalized"), world["sessions"]() as s, s.begin():  # a reopen while open
        s.add(
            FinalizationEvent(
                submission_id=sid,
                action="REOPENED",
                snapshot_id=uuid.UUID(snap_id),
                reason="reopened twice in a row",
                actor_id=world["admin"],
            )
        )


# -------------------------------------------------------------------------------------------------- reopen


def test_reopen_needs_a_reason_is_audited_and_the_next_finalize_adds_a_snapshot(
    world: dict[str, Any], ex: dict[str, Any]
) -> None:
    b = graded(world, ex, "RO-1", q1="part")
    assert reopen(world, b, "long enough reason").status_code == 409  # not finalized yet
    first = finalize(world, b).json()
    assert first["total"] == "1"
    assert reopen(world, b, "too short").status_code == 422
    assert reopen(world, b, "          ").status_code == 422
    r = reopen(world, b, "  Q1 re-marked after the moderation meeting.  ")
    assert r.status_code == 200 and r.json()["state"] == "OPEN"
    hist = r.json()["history"]
    assert [(h["action"], h["snapshot_no"]) for h in hist] == [("FINALIZED", 1), ("REOPENED", 1)]
    assert hist[1]["reason"] == "Q1 re-marked after the moderation meeting." and hist[1]["by"] == "teacher"
    assert reopen(world, b, "long enough reason again").status_code == 409  # already open

    assert grade(world, b, "q1", 1, {"c1": "full"}, who="exA").status_code in (200, 422)  # grading is allowed again
    s2 = finalize(world, b)
    assert s2.status_code == 201 and s2.json()["snapshot_no"] == 2 and s2.json()["total"] == "2"
    snaps = world["client"].get(f"/api/submissions/{b['sub']}/snapshots", headers=login(world, "teacher")).json()
    assert [(s["snapshot_no"], s["total"]) for s in snaps] == [(1, "1"), (2, "2")]  # snapshot 1 is untouched
    assert audit_actions(world, b["sub"]) == ["submission.finalize", "submission.reopen", "submission.finalize"]
    with world["sessions"]() as s:
        reopen_row = s.scalars(
            select(AuditLog).where(AuditLog.action == "submission.reopen", AuditLog.entity_id == b["sub"])
        ).one()
        assert (
            reopen_row.details["reason"] == "Q1 re-marked after the moderation meeting."
            and reopen_row.details["snapshot_no"] == 1
        )
    assert verified(world, submission_id=uuid.UUID(b["sub"])).ok  # both snapshots reproduce


def test_finalize_the_exam_takes_the_ready_booklets_and_lists_the_rest(world: dict[str, Any], ex: dict[str, Any]) -> None:
    ready = graded(world, ex, "BK-1")
    empty = booklet(world, ex, "BK-2")
    r = world["client"].post(
        f"/api/exams/{ex['exam']}/finalize", json={"confirm_not_attempted": True}, headers=login(world, "teacher")
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["finalized"] >= 1
    assert fin(world, ready)["state"] == "FINALIZED" and fin(world, empty)["state"] == "OPEN"
    mine = [x for x in out["skipped"] if x["submission_id"] == empty["sub"]]
    assert len(mine) == 1 and mine[0]["code"] == "not_ready" and "No answer boxes" in mine[0]["message"]
    assert world["client"].post(f"/api/exams/{ex['exam']}/finalize", json={}, headers=login(world, "exA")).status_code == 403


# -------------------------------------------------------------------------------------------------- concurrency


def test_a_grading_write_in_flight_and_a_finalize_cannot_interleave(world: dict[str, Any], ex: dict[str, Any]) -> None:
    """A grade whose transaction is open holds the booklet; finalize waits for it, then freezes it INCLUDING that grade."""
    b = graded(world, ex, "CC-1", q1="part")
    sid = uuid.UUID(b["sub"])
    started, done = threading.Event(), threading.Event()
    result: dict[str, Any] = {}

    def finalize_in_thread() -> None:
        started.set()
        result["r"] = finalize(world, b)
        done.set()

    with world["sessions"]() as s, s.begin():
        # an evaluation insert in flight (uncommitted): it takes the booklet's share lock through the guard trigger
        s.execute(
            text(
                "INSERT INTO evaluations (id, submission_id, rubric_version_id, qid, attempt_no, verdicts, notes, marks, examiner_id, "
                "is_override, score_computer_version, supersedes_id) "
                'SELECT gen_random_uuid(), submission_id, rubric_version_id, qid, attempt_no, \'{"c1": "full"}\'::jsonb, notes, 2, '
                "examiner_id, false, score_computer_version, id FROM evaluations WHERE submission_id = :s ORDER BY seq DESC LIMIT 1"
            ),
            {"s": sid},
        )
        t = threading.Thread(target=finalize_in_thread)
        t.start()
        started.wait(5)
        assert not done.wait(1.0), "finalize must wait for the grading write that is still in flight"
    t.join(20)
    assert done.is_set() and result["r"].status_code == 201, result
    assert result["r"].json()["total"] == "2"  # the late grade (full = 2) is in the frozen result, not lost
    assert verified(world, submission_id=sid).ok


# -------------------------------------------------------------------------------------------------- verify (I12)


def _disable(world: dict[str, Any], table: str, trigger: str, on: bool) -> None:
    with world["sessions"]() as s, s.begin():
        s.execute(text(f"ALTER TABLE {table} {'ENABLE' if on else 'DISABLE'} TRIGGER {trigger}"))


def test_invariant_I12_verify_recomputes_from_raw_records_and_fails_loudly(
    world: dict[str, Any], ex: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    b = graded(world, ex, "V-1")
    assert finalize(world, b).status_code == 201
    sid = uuid.UUID(b["sub"])
    monkeypatch.setenv("GRADEMIND_ENV", "test")
    monkeypatch.setenv("GRADEMIND_DATABASE_URL", world["settings"].database_url)
    assert cli_main(["verify-snapshots", "--submission", b["sub"]]) == 0
    assert "VERIFY OK: 1 snapshot(s)" in capsys.readouterr().out

    # 1) the frozen total is altered behind the application's back
    _disable(world, "result_snapshots", "result_snapshots_append_only", False)
    try:
        with world["sessions"]() as s, s.begin():
            s.execute(text("UPDATE result_snapshots SET total = total + 1 WHERE submission_id = :s"), {"s": sid})
        assert cli_main(["verify-snapshots", "--submission", b["sub"]]) == 1
        err = capsys.readouterr().err
        assert "VERIFY FAILED" in err and "total: frozen 3" in err and "recomputed 2" in err
        with world["sessions"]() as s, s.begin():
            s.execute(text("UPDATE result_snapshots SET total = total - 1 WHERE submission_id = :s"), {"s": sid})
    finally:
        _disable(world, "result_snapshots", "result_snapshots_append_only", True)
    assert cli_main(["verify-snapshots", "--submission", b["sub"]]) == 0

    # 2) a raw verdict is altered behind the application's back (the recompute from raw records must notice)
    _disable(world, "evaluations", "evaluations_append_only", False)
    try:
        with world["sessions"]() as s, s.begin():
            s.execute(text('UPDATE evaluations SET verdicts = \'{"c1": "none"}\'::jsonb WHERE submission_id = :s'), {"s": sid})
        capsys.readouterr()
        assert cli_main(["verify-snapshots", "--submission", b["sub"]]) == 1
        err = capsys.readouterr().err
        assert "inputs: the digest" in err and "total: frozen 2, recomputed 0" in err
    finally:
        with world["sessions"]() as s, s.begin():
            s.execute(text('UPDATE evaluations SET verdicts = \'{"c1": "full"}\'::jsonb WHERE submission_id = :s'), {"s": sid})
        _disable(world, "evaluations", "evaluations_append_only", True)
    assert cli_main(["verify-snapshots", "--submission", b["sub"]]) == 0
    assert cli_main(["verify-snapshots", "--exam", "not-an-id"]) == 2
    assert verified(world, exam_id=uuid.UUID(ex["exam"])).ok


def test_verify_notices_a_changed_score_computer_version(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "V-2")
    assert finalize(world, b).status_code == 201
    _disable(world, "result_snapshots", "result_snapshots_append_only", False)
    try:
        with world["sessions"]() as s, s.begin():
            s.execute(
                text("UPDATE result_snapshots SET score_computer_version = 'score-computer-0.9.0' WHERE submission_id = :s"),
                {"s": uuid.UUID(b["sub"])},
            )
        with world["sessions"]() as s:
            rep = verify_all(s, submission_id=uuid.UUID(b["sub"]))
        assert not rep.ok and any("cannot be re-verified" in p for ps in rep.failures.values() for p in ps)
    finally:
        with world["sessions"]() as s, s.begin():
            s.execute(
                text("UPDATE result_snapshots SET score_computer_version = :v WHERE submission_id = :s"),
                {"v": _current_version(), "s": uuid.UUID(b["sub"])},
            )
        _disable(world, "result_snapshots", "result_snapshots_append_only", True)


def _current_version() -> str:
    from grademind_core.scoring import VERSION

    return VERSION


def test_snapshots_hold_what_is_needed_to_recompute(world: dict[str, Any], ex: dict[str, Any]) -> None:
    b = graded(world, ex, "V-3")
    assert finalize(world, b).status_code == 201
    with world["sessions"]() as s:
        snap = s.scalars(select(ResultSnapshot).where(ResultSnapshot.submission_id == uuid.UUID(b["sub"]))).one()
        assert snap.attempts == [{"qid": "q1", "attempt_no": 1, "crossed_out": False}]
        assert len(snap.evaluation_refs) == 1 and len(snap.inputs_sha256) == 64
        ev = s.get(Evaluation, uuid.UUID(snap.evaluation_refs[0]["evaluation_id"]))
        assert ev is not None and ev.verdicts == {"c1": "full"}
        assert isinstance(snap.policy, dict) and snap.score_computer_version == _current_version()
