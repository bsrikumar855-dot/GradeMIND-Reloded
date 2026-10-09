"""The shadow-path scanner must pass on the repo AND fire on planted violations (a check that never fires proves nothing)."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_single_paths", ROOT / "scripts" / "check_single_paths.py")
assert _spec and _spec.loader
csp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csp)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for d in csp.PY_DIRS + ["apps/web"]:
        src = ROOT / d
        if src.is_dir():
            shutil.copytree(
                src, tmp_path / d, ignore=shutil.ignore_patterns("__pycache__", ".venv", "node_modules", ".next", "tests")
            )
    return tmp_path


def test_repository_is_clean() -> None:
    assert csp.scan(ROOT) == []


def plant(tree: Path, rel: str, code: str) -> None:
    p = tree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code)


@pytest.mark.parametrize(
    ("rel", "code", "needle"),
    [
        ("apps/api/src/grademind_api/routes/x.py", "import minio\n", "imports minio"),
        ("apps/worker/src/grademind_worker/x.py", "from celery import Celery\n", "imports celery"),
        ("packages/core/src/grademind_core/x.py", "import anthropic\n", "imports anthropic"),
        ("packages/core/src/grademind_core/x.py", "from google.genai import Client\n", "imports google.genai"),
        ("apps/api/src/grademind_api/x.py", "m = importlib.import_module('openai')\n", "imports openai"),
        ("services/ocr/src/grademind_ocr/x.py", "import paddleocr\n", "imports paddleocr"),
        ("apps/api/src/grademind_api/x.py", "class ObjectStore:\n    pass\n", "ObjectStore"),
        ("apps/worker/src/grademind_worker/x.py", "def run_job(x):\n    pass\n", "run_job"),
        ("apps/api/src/grademind_api/x.py", "final_score = a + b\n", "score arithmetic"),
        ("packages/core/src/grademind_core/x.py", "awarded_marks += 2\n", "score arithmetic"),
    ],
)
def test_planted_violations_are_caught(tree: Path, rel: str, code: str, needle: str) -> None:
    assert csp.scan(tree) == []
    plant(tree, rel, code)
    found = csp.scan(tree)
    assert any(needle in v for v in found), found


def test_llm_sdk_in_web_is_caught(tree: Path) -> None:
    pkg = tree / "apps/web/package.json"
    d = json.loads(pkg.read_text())
    d["dependencies"]["@anthropic-ai/sdk"] = "1.0.0"
    pkg.write_text(json.dumps(d))
    assert any("@anthropic-ai/sdk" in v for v in csp.scan(tree))


def test_comparisons_are_not_score_arithmetic(tree: Path) -> None:
    plant(tree, "apps/api/src/grademind_api/x.py", "if final_score == 3:\n    pass\n")
    assert csp.scan(tree) == []
