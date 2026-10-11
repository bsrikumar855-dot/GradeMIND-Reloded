"""5.6 (D30.2): the pilot error-rate report: refuses below 50 lines, gives an interval and n, never prints student text."""

from __future__ import annotations

import types
from contextlib import nullcontext
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grademind_core import cli, pilot_stats
from grademind_core.pilot_stats import CAVEAT, MIN_N, TARGET_N, Pair, TooFewLinesError, cer_report, levenshtein, render

STUDENT_TEXT = "ZEBRAQUARTZ-private-answer"  # stands for student text: it must never appear in anything printed


def pairs(n: int, machine: str = "teh cat", final: str = "the cat") -> list[Pair]:
    return [Pair(machine, final) for _ in range(n)]


def test_edit_distance_counts_characters_not_bytes() -> None:
    assert levenshtein("", "") == 0 and levenshtein("abc", "") == 3 and levenshtein("", "abc") == 3
    assert levenshtein("kitten", "sitting") == 3 and levenshtein("teh", "the") == 2
    assert levenshtein("தமிழ்", "தமிழ") == 1  # one code point (the virama), not one byte


@settings(max_examples=60, deadline=None)
@given(st.text(max_size=25), st.text(max_size=25))
def test_edit_distance_is_symmetric_and_bounded(a: str, b: str) -> None:
    d = levenshtein(a, b)
    assert d == levenshtein(b, a) and abs(len(a) - len(b)) <= d <= max(len(a), len(b)) and (d == 0) == (a == b)


def test_the_rate_is_total_edits_over_total_characters_with_an_interval() -> None:
    data = pairs(30) + pairs(30, "the cat", "the cat") + pairs(10, "hello wrld", "hello world")
    r = cer_report(data)
    assert r.n_lines == 70 and r.n_chars == 30 * 7 + 30 * 7 + 10 * 11 and r.edits == 30 * 2 + 10 * 1
    assert r.cer == pytest.approx(r.edits / r.n_chars)
    assert r.cer_low <= r.cer <= r.cer_high and r.cer_high - r.cer_low > 0
    assert r.mean_line_cer == pytest.approx((30 * 2 / 7 + 10 * 1 / 11) / 70)
    assert r.exact_after_edit_le_1 == pytest.approx((30 + 10) / 70) and r.interim is True
    assert cer_report(data) == r  # the same data always gives the same interval (fixed seed)


def test_it_refuses_below_the_minimum_and_does_not_count_empty_references() -> None:
    assert MIN_N == 50
    with pytest.raises(TooFewLinesError) as e:
        cer_report(pairs(MIN_N - 1))
    assert e.value.n == MIN_N - 1 and "at least 50" in str(e.value)
    with pytest.raises(TooFewLinesError):
        cer_report(pairs(MIN_N - 1) + [Pair("something", "   ")])  # a line with no final text is not a line to measure
    assert cer_report(pairs(MIN_N)).n_lines == MIN_N


def test_interim_until_the_protocol_number_is_reached() -> None:
    assert cer_report(pairs(TARGET_N - 1)).interim is True
    assert cer_report(pairs(TARGET_N)).interim is False


def test_the_report_text_has_numbers_the_caveat_and_no_student_text() -> None:
    r = cer_report([Pair(f"{STUDENT_TEXT} teh", f"{STUDENT_TEXT} the") for _ in range(60)], missed_lines=4, unreadable_left_out=2)
    text = render(r)
    assert STUDENT_TEXT not in text and "60 corrected line(s)" in text and "INTERIM" in text
    assert "95% interval" in text and "missed (added by an examiner): 4" in text and "unreadable ([?]): 2" in text
    assert CAVEAT in text and "AMONG THE LINES AN EXAMINER CORRECTED" in text and "not an accuracy" in text


def test_the_command_prints_numbers_only_and_refuses_when_there_are_too_few(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    served: dict[str, Any] = {"n": MIN_N - 1}

    def fake_load(db: Any, exam_ids: Any, limit: int) -> tuple[list[Pair], int, int]:
        served["args"] = (exam_ids, limit)
        return [Pair(f"{STUDENT_TEXT} teh", f"{STUDENT_TEXT} the")] * served["n"], 3, 1

    monkeypatch.setattr(cli, "get_settings", lambda: types.SimpleNamespace(database_url="postgresql://unused"))
    monkeypatch.setattr(cli, "session_factory", lambda url: lambda: nullcontext(None))
    monkeypatch.setattr(cli, "load_pairs", fake_load)
    assert cli.main(["pilot-cer"]) == 2
    out = capsys.readouterr()
    assert "refused" in out.err and "at least 50" in out.err and out.out == "" and STUDENT_TEXT not in out.err  # no figure at all
    served["n"] = 80
    exam = "5b0ba63f-2d22-4d6f-8d0e-6f8d5a9a3f10"
    assert cli.main(["pilot-cer", "--exam", exam, "--limit", "100"]) == 0
    out = capsys.readouterr()
    assert "80 corrected line(s)" in out.out and STUDENT_TEXT not in out.out + out.err and "Character error rate" in out.out
    assert [str(x) for x in served["args"][0]] == [exam] and served["args"][1] == 100


def test_the_module_only_reads_the_corrections_table() -> None:
    src = pilot_stats.__loader__.get_source(pilot_stats.__name__) or ""  # type: ignore[union-attr]
    assert "db.add" not in src and ".commit(" not in src and "delete(" not in src and "update(" not in src
