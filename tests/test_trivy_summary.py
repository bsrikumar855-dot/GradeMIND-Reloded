"""5.4 (D30.6): the Trivy summary is a report: counts per severity, what has a fix, never a failure (synthetic report)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trivy_summary", ROOT / "scripts" / "trivy_summary.py")
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

REPORT = {
    "ArtifactName": "example:1@sha256:abc",
    "Metadata": {"OS": {"Family": "alpine", "Name": "3.23"}},
    "Results": [
        {
            "Target": "example",
            "Class": "os-pkgs",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-1",
                    "PkgName": "a",
                    "InstalledVersion": "1",
                    "Severity": "CRITICAL",
                    "FixedVersion": "2",
                },
                {"VulnerabilityID": "CVE-2", "PkgName": "b", "InstalledVersion": "1", "Severity": "HIGH"},
                {"VulnerabilityID": "CVE-3", "PkgName": "c", "InstalledVersion": "1", "Severity": "HIGH", "FixedVersion": "1.1"},
                {"VulnerabilityID": "CVE-4", "PkgName": "d", "InstalledVersion": "1", "Severity": "LOW"},
                {"VulnerabilityID": "CVE-5", "PkgName": "e", "InstalledVersion": "1", "Severity": "SURPRISE"},
            ],
        },
        {"Target": "empty", "Class": "lang-pkgs", "Vulnerabilities": None},
    ],
}


def test_counts_fixable_and_worst_order() -> None:
    s = mod.summarise(REPORT)
    assert s["counts"] == {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 0, "LOW": 1, "UNKNOWN": 1}  # an unknown label is counted, not lost
    assert s["fixable"]["CRITICAL"] == 1 and s["fixable"]["HIGH"] == 1
    assert [v["VulnerabilityID"] for v in s["worst"]] == ["CVE-1", "CVE-3", "CVE-2"]  # critical first, fixable before unfixable
    assert s["os"] == "alpine 3.23" and s["total"] == 5


def test_render_table_and_no_findings() -> None:
    text = mod.render([mod.summarise(REPORT), mod.summarise({"ArtifactName": "clean:1"})])
    assert "| `example:1@sha256:abc` | alpine 3.23 | 1 | 2 | 0 | 1 | 2 of 3 |" in text
    assert "| `clean:1` | n/a | 0 | 0 | 0 | 0 | 0 of 0 |" in text
    assert "CRITICAL CVE-1: `a` 1 (fixed in 2)" in text and "HIGH CVE-2: `b` 1 (no fix yet)" in text


def test_main_never_fails_and_writes_markdown(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text(json.dumps(REPORT))
    out = tmp_path / "summary.md"
    r = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "trivy_summary.py"), str(tmp_path), "--markdown", str(out)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "CVE-1" in out.read_text()
    empty = tmp_path / "none"
    empty.mkdir()
    r = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "trivy_summary.py"), str(empty)], capture_output=True, text=True
    )
    assert r.returncode == 0 and "No Trivy reports found." in r.stdout
