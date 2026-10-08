"""Process rule 10: every degenerate-detector rule needs a real-page regression fixture plus a clean-page
false-positive check. Fixtures are real student OCR output, so they live in gitignored data/fixtures/
(see data/fixtures/README.md); these tests SKIP, loudly, when the fixtures are absent.

Run: uvx --from pytest==9.1.1 pytest spike/tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "spike" / "engine_a"))
from degenerate import degenerate_checks  # noqa: E402

FIX = ROOT / "data" / "fixtures"
needs_fixtures = pytest.mark.skipif(not (FIX / "degenerate").is_dir(),
                                    reason="data/fixtures absent (gitignored student data); detector tests NOT run")

# fixture file -> flag prefixes that MUST appear
EXPECTED = {
    "int8_s1p02_charrun_dupes.json": ["CHAR_RUN_", "NEAR_DUPLICATE_REGION_PAIRS_", "REPEATED_BBOX_"],
    "int8_s1p06_word_repeat.json": ["WORD_REPEAT_"],
    "int8_s1p11_empty_cells.json": ["EMPTY_CELL_SPAM_"],
    "int8_s2p02_cjk_repeated_region.json": ["REPEATED_REGION_TEXT_", "UNEXPECTED_SCRIPT_CJK_"],
    "base_s1p02_repeated_cell.json": ["REPEATED_CELL_TEXT_"],
    "base_s1p11_counting.json": ["COUNTING_SEQUENCE_", "EMPTY_CELL_SPAM_"],
    "bf16_s1p02_counting_runaway.json": ["COUNTING_SEQUENCE_", "RUNAWAY_LENGTH_"],
    "vllm_s1p11_finish_length.json": ["FINISH_REASON_LENGTH"],
}
# Rules with no real-page fixture yet. Listed so the gap is visible, not silently "covered".
NO_REAL_FIXTURE = ["TRUNCATED_AT_MAX_LENGTH", "REPEATED_LINE_", "EMPTY_OUTPUT", "ALL_REGIONS_EMPTY"]


def flags_for(rec: dict) -> list[str]:
    return degenerate_checks(rec["raw"], rec["generated_tokens"], rec["regions"],
                             rec.get("decoding", {}).get("max_length", 32768), rec.get("finish_reason"))


@needs_fixtures
@pytest.mark.parametrize("name,prefixes", sorted(EXPECTED.items()))
def test_rule_fires_on_real_degenerate_page(name: str, prefixes: list[str]) -> None:
    flags = flags_for(json.loads((FIX / "degenerate" / name).read_text()))
    for prefix in prefixes:
        assert any(f.startswith(prefix) for f in flags), f"{name}: expected {prefix}*, got {flags}"


@needs_fixtures
def test_no_flags_on_reviewed_clean_pages() -> None:
    clean = sorted((FIX / "clean").glob("*.json"))
    assert len(clean) >= 20, "too few clean fixtures for a meaningful false-positive check"
    flagged = {p.name: flags_for(json.loads(p.read_text())) for p in clean}
    flagged = {k: v for k, v in flagged.items() if v}
    assert not flagged, f"false positives on clean pages: {flagged}"


SYN = Path(__file__).parent / "fixtures_synthetic"


@pytest.mark.parametrize("rule", NO_REAL_FIXTURE)
def test_rule_fires_on_synthetic_fixture(rule: str) -> None:
    """SYNTHETIC fixtures (committed): prove the rule fires; they do NOT count as real-page coverage."""
    rec = json.loads((SYN / f"SYNTHETIC_{rule.strip('_')}.json").read_text())
    assert rec["_label"] == "SYNTHETIC"
    assert any(f.startswith(rule) for f in flags_for(rec)), f"{rule} did not fire: {flags_for(rec)}"


@pytest.mark.parametrize("rule", NO_REAL_FIXTURE)
def test_rules_without_real_fixture_are_reported(rule: str) -> None:
    pytest.skip(f"{rule}: no real-page fixture yet (process rule 10 gap; SYNTHETIC fixture only)")
