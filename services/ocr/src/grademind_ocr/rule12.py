"""Rule 12: resolved config, not requested config. Pure functions (no Paddle import), unit-tested in CI.

The service compares what the library ACTUALLY loaded (model names read back from the instantiated pipeline, weight
files hashed on disk, library versions) against `expected_models.json`. Any difference is a startup failure.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_PATH = Path(__file__).resolve().parents[2] / "expected_models.json"


class Rule12Violation(RuntimeError):
    pass


def load_expected(path: Path = EXPECTED_PATH) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def fingerprint(model_dir: Path, files: list[str]) -> dict[str, str | None]:
    return {f: (sha256_file(model_dir / f) if (model_dir / f).is_file() else None) for f in files}


def problems(expected: dict[str, Any], resolved: dict[str, Any]) -> list[str]:
    """Every difference between the expected and the resolved configuration, as human-readable lines."""
    out: list[str] = []
    for lib, want in expected["libraries"].items():
        got = resolved.get("libraries", {}).get(lib)
        if got != want:
            out.append(f"library {lib}: expected {want!r}, resolved {got!r}")
    for role, spec in expected["models"].items():
        r = resolved.get("models", {}).get(role, {})
        if r.get("name") != spec["name"]:
            out.append(f"{role} model: expected {spec['name']!r}, resolved {r.get('name')!r}")
        for f, digest in spec["sha256"].items():
            got = r.get("sha256", {}).get(f)
            if got != digest:
                out.append(f"{role} weights {f}: expected sha256 {digest[:12]}..., resolved {str(got)[:12]}...")
    for k, want in expected["pipeline_resolved"].items():
        got = resolved.get("pipeline", {}).get(k)
        if got != want:
            out.append(f"pipeline {k}: expected {want!r}, resolved {got!r}")
    return out


def assert_resolved(expected: dict[str, Any], resolved: dict[str, Any]) -> None:
    found = problems(expected, resolved)
    if found:
        raise Rule12Violation("RULE 12 VIOLATION (resolved != expected): " + "; ".join(found))
