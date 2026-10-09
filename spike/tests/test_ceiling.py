"""Phase 0c ceiling runner: pure parts only (no network, no SDK calls)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "spike"))


def _load():
    spec = importlib.util.spec_from_file_location("run_ceiling", ROOT / "spike" / "ceiling" / "run_ceiling.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gemini_selection_rule_picks_highest_stable_pro() -> None:
    m = _load()
    listed = [
        {"name": "models/gemini-3.0-pro", "supported_actions": ["generateContent"]},
        {"name": "models/gemini-3.5-pro", "supported_actions": ["generateContent"]},
        {"name": "models/gemini-3.6-pro-preview", "supported_actions": ["generateContent"]},
        {"name": "models/gemini-pro-latest", "supported_actions": ["generateContent"]},
        {"name": "models/gemini-3.5-flash", "supported_actions": ["generateContent"]},
        {"name": "models/gemini-4.0-pro", "supported_actions": ["embedContent"]},
    ]
    name, cands = m.select_gemini(listed)
    assert name == "gemini-3.5-pro"
    assert [c["name"] for c in cands] == ["gemini-3.5-pro", "gemini-3.0-pro"]


def test_gemini_selection_rule_none() -> None:
    assert _load().select_gemini([{"name": "models/gemini-3.5-flash", "supported_actions": ["generateContent"]}])[0] is None


def test_prompt_is_extracted_verbatim_and_pinned() -> None:
    m = _load()
    text, sha = m.load_prompt()
    assert text.startswith("You are transcribing one page") and "Never fix or normalise anything" in text
    assert "BEGIN PROMPT" not in text and len(sha) == 64


def test_only_25_redacted_answer_pages_no_covers() -> None:
    pg = _load().pages()
    assert len(pg) == 25 and all(p[1] != "page_01" for p in pg)
    assert all("data/redacted/" in str(p[2]) for p in pg)


def test_text_reader_drops_rule12_mismatch() -> None:
    from score import READERS
    assert READERS["text"]({"rule12": "OK", "lines_text": ["a", " ", "b"]}) == ["a", "b"]
    assert READERS["text"]({"rule12": "RULE12_MISMATCH", "lines_text": ["a"]}) == []
