"""Summarise Trivy JSON reports into the owner-readable severity table (D30.6).

Usage: python3 scripts/trivy_summary.py DIR [--markdown FILE]

Per image: how many findings of each severity, how many of those have a fix available (a newer package version exists:
an image rebuild or a base-image update would remove them), and the worst ones with their CVE id.
A report only: it never fails a build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
WORST = ("CRITICAL", "HIGH")


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for res in report.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            out.append({**v, "_target": res.get("Target", ""), "_class": res.get("Class", "")})
    return out


def summarise(report: dict[str, Any]) -> dict[str, Any]:
    fs = findings(report)
    counts = {s: 0 for s in SEVERITIES}
    fixable = {s: 0 for s in SEVERITIES}
    for f in fs:
        sev = f.get("Severity", "UNKNOWN")
        sev = sev if sev in counts else "UNKNOWN"
        counts[sev] += 1
        if f.get("FixedVersion"):
            fixable[sev] += 1
    worst = sorted(
        (f for f in fs if f.get("Severity") in WORST),
        key=lambda f: (
            SEVERITIES.index(f["Severity"]),
            not f.get("FixedVersion"),
            f.get("PkgName", ""),
            f.get("VulnerabilityID", ""),
        ),
    )
    os_info = (report.get("Metadata") or {}).get("OS") or {}
    return {
        "image": report.get("ArtifactName", "?"),
        "os": f"{os_info.get('Family', '')} {os_info.get('Name', '')}".strip(),
        "counts": counts,
        "fixable": fixable,
        "worst": worst,
        "total": len(fs),
    }


def render(summaries: list[dict[str, Any]], worst_per_image: int = 6) -> str:
    lines = [
        "| Image | OS | Critical | High | Medium | Low | High+Critical with a fix |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        c, f = s["counts"], s["fixable"]
        lines.append(
            f"| `{s['image']}` | {s['os'] or 'n/a'} | {c['CRITICAL']} | {c['HIGH']} | {c['MEDIUM']} | {c['LOW']} "
            f"| {f['CRITICAL'] + f['HIGH']} of {c['CRITICAL'] + c['HIGH']} |"
        )
    for s in summaries:
        if not s["worst"]:
            continue
        lines += ["", f"Worst findings in `{s['image']}` (first {worst_per_image}):", ""]
        for v in s["worst"][:worst_per_image]:
            fix = f"fixed in {v['FixedVersion']}" if v.get("FixedVersion") else "no fix yet"
            what = f"`{v.get('PkgName', '?')}` {v.get('InstalledVersion', '')}"
            lines.append(f"- {v['Severity']} {v.get('VulnerabilityID', '?')}: {what} ({fix})")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: trivy_summary.py DIR [--markdown FILE]", file=sys.stderr)
        return 2
    paths = sorted(Path(argv[0]).glob("*.json"))
    summaries = [summarise(load(p)) for p in paths]
    text = render(summaries) if summaries else "No Trivy reports found."
    print(text)
    if "--markdown" in argv:
        Path(argv[argv.index("--markdown") + 1]).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
