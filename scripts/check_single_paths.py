"""No shadow paths (spec rule 3, rule 7). Complements import-linter with checks it cannot express:

1. provider/infrastructure SDKs imported outside their approved adapter, including dynamic imports
   (`importlib.import_module("anthropic")`, `__import__("minio")`) that import-linter does not see;
2. LLM SDKs added to the web app (D19: no AI path in v1; no keys may reach the frontend);
3. single-path components defined more than once (a second ObjectStore, ProviderRegistry, run_job, Settings);
4. score arithmetic outside the ScoreComputer module (none exists yet in Phase 1, so any occurrence fails).

Usage: python3 scripts/check_single_paths.py [ROOT]   -> prints violations, exit 1 if any.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PY_DIRS = ["packages", "apps/api", "apps/worker", "services"]

# SDK top-level module -> files allowed to import it (repo-relative)
SDK_ALLOWED: dict[str, set[str]] = {
    "minio": {"packages/core/src/grademind_core/storage.py"},
    "boto3": set(),
    "botocore": set(),
    "celery": {"apps/api/src/grademind_api/queue.py", "apps/worker/src/grademind_worker/celery_app.py"},
    "paddle": {"services/ocr/src/grademind_ocr/engine.py"},
    "paddleocr": {"services/ocr/src/grademind_ocr/engine.py"},
    "paddlex": set(),
    "cv2": {"services/ocr/src/grademind_ocr/engine.py"},
    "torch": set(),
    "transformers": set(),
    "anthropic": set(),
    "openai": set(),
    "google.genai": set(),
    "google.generativeai": set(),
    "vllm": set(),
    "litellm": set(),
}

# exactly one definition each, at the listed path
SINGLE_DEFINITIONS: dict[str, str] = {
    r"^class ObjectStore\b": "packages/core/src/grademind_core/storage.py",
    r"^class ProviderRegistry\b": "packages/core/src/grademind_core/providers.py",
    r"^def run_job\b": "packages/core/src/grademind_core/jobs.py",
    r"^class Settings\b": "packages/core/src/grademind_core/config.py",
}

# score arithmetic is allowed only in the ScoreComputer module (spec I-invariants); it does not exist yet
SCORE_ALLOWED: set[str] = {"packages/core/src/grademind_core/scoring.py"}
SCORE_PATTERN = re.compile(
    r"\b(awarded_marks|marks_awarded|final_score|final_marks|score_total|total_awarded)\b\s*(\+|-|\*|/|=(?!=)|\+=|-=)"
)

WEB_FORBIDDEN_DEPS = re.compile(r"^(@anthropic-ai/|openai$|@google/genai$|@google/generative-ai$|ai$|@ai-sdk/|minio$|@aws-sdk/)")


def _sdk_patterns(mod: str) -> list[re.Pattern[str]]:
    m = re.escape(mod)
    return [
        re.compile(rf"^\s*import\s+{m}(\.|\s|$|,)", re.M),
        re.compile(rf"^\s*from\s+{m}(\.|\s)", re.M),
        re.compile(rf"""(import_module|__import__)\(\s*["']{m}(["'.])"""),
    ]


def scan(root: Path) -> list[str]:
    out: list[str] = []
    files = [
        p for d in PY_DIRS for p in sorted((root / d).rglob("*.py")) if "__pycache__" not in p.parts and ".venv" not in p.parts
    ]
    defs: dict[str, list[str]] = {k: [] for k in SINGLE_DEFINITIONS}
    for p in files:
        rel = p.relative_to(root).as_posix()
        text = p.read_text(errors="replace")
        for mod, allowed in SDK_ALLOWED.items():
            if rel in allowed:
                continue
            for pat in _sdk_patterns(mod):
                for mt in pat.finditer(text):
                    line = text.count("\n", 0, mt.start()) + 1
                    out.append(
                        f"{rel}:{line}: imports {mod} outside its adapter ({sorted(allowed) or 'not allowed anywhere in v1'})"
                    )
        for pat, _ in SINGLE_DEFINITIONS.items():
            if re.search(pat, text, re.M):
                defs[pat].append(rel)
        if rel not in SCORE_ALLOWED and "/tests/" not in rel:
            for mt in SCORE_PATTERN.finditer(text):
                line = text.count("\n", 0, mt.start()) + 1
                out.append(f"{rel}:{line}: score arithmetic outside ScoreComputer ({mt.group(0).strip()})")
    for pat, where in SINGLE_DEFINITIONS.items():
        if defs[pat] != [where]:
            out.append(f"{pat!r} must be defined exactly once, in {where}; found in {defs[pat]}")
    pkg = root / "apps/web/package.json"
    if pkg.exists():
        d = json.loads(pkg.read_text())
        for name in {**d.get("dependencies", {}), **d.get("devDependencies", {})}:
            if WEB_FORBIDDEN_DEPS.match(name):
                out.append(f"apps/web/package.json: {name} is not allowed in the web app (D19 / no keys in the frontend)")
    return out


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    found = scan(root)
    for v in found:
        print("SHADOW PATH:", v)
    print(f"check_single_paths: {len(found)} violation(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
