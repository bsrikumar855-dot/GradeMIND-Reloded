"""D28: machine-read text must never reach a verdict. The import-linter contract "OCR text never reaches a verdict (D28)" is
only worth something if it demonstrably FIRES, so this test copies the source tree, plants OCR imports into the grading
path (one direct, one through an intermediate module) and requires the contract to break; the untouched copy must pass."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINT = Path(sys.executable).parent / "lint-imports"
CONTRACT = "OCR text never reaches a verdict (D28)"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for d in ("packages", "apps/api", "apps/worker", "services/ocr"):
        shutil.copytree(
            ROOT / d,
            tmp_path / d,
            ignore=shutil.ignore_patterns(
                "__pycache__", ".venv", "node_modules", ".next", "tests", "wheelhouse", "models", "vendor-cache"
            ),
        )
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path


def lint(tree: Path) -> tuple[int, str]:
    src = [tree / "packages/core/src", tree / "apps/api/src", tree / "apps/worker/src", tree / "services/ocr/src"]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(p) for p in src)}
    r = subprocess.run([str(LINT)], cwd=tree, env=env, capture_output=True, text=True, check=False)  # noqa: S603
    return r.returncode, r.stdout + r.stderr


def contract_line(out: str) -> str:
    return next((ln for ln in out.splitlines() if CONTRACT in ln), "")


def test_the_untouched_tree_satisfies_the_contract(tree: Path) -> None:
    code, out = lint(tree)
    assert code == 0 and "KEPT" in contract_line(out), out[-1500:]


@pytest.mark.parametrize(
    ("victim", "planted"),
    [
        ("packages/core/src/grademind_core/scoring.py", "from grademind_core.ocr_runs import config_hash  # planted\n"),
        ("packages/core/src/grademind_core/evaluation.py", "from grademind_core.db.ocr_models import OcrLine  # planted\n"),
        ("apps/api/src/grademind_api/routes/grading.py", "from grademind_api.routes.ocr import router as _ocr  # planted\n"),
    ],
)
def test_a_direct_ocr_import_into_the_grading_path_breaks_the_contract(tree: Path, victim: str, planted: str) -> None:
    p = tree / victim
    p.write_text(p.read_text() + "\n" + planted)
    code, out = lint(tree)
    assert code != 0 and "BROKEN" in contract_line(out), out[-1500:]


def test_an_indirect_path_through_an_innocent_looking_module_breaks_the_contract(tree: Path) -> None:
    helper = tree / "packages/core/src/grademind_core/pretty_marks.py"
    helper.write_text("from grademind_core.ocr_client import OcrServiceClient  # noqa: F401\n")
    grading = tree / "packages/core/src/grademind_core/grading.py"
    grading.write_text(grading.read_text() + "\nfrom grademind_core import pretty_marks  # noqa: F401  planted\n")
    code, out = lint(tree)
    assert code != 0 and "BROKEN" in contract_line(out), out[-1500:]
