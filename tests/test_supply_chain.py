"""D26.2: every container image is pinned by digest and every GitHub Action by commit SHA; the OCR build is vendored."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILES = [ROOT / "docker/python-app.Dockerfile", ROOT / "services/ocr/Dockerfile", ROOT / "apps/web/Dockerfile"]
LOCAL_IMAGES = {"grademind-app:dev", "grademind-ocr:dev", "grademind-web:dev"}  # built from this repo


def test_dockerfile_base_images_are_pinned_by_digest() -> None:
    froms = [(p.name, m) for p in DOCKERFILES for m in re.findall(r"^FROM\s+(\S+)", p.read_text(), re.M)]
    assert froms
    assert [f for f in froms if "@sha256:" not in f[1]] == []


def test_compose_and_ci_images_are_pinned_by_digest() -> None:
    for f in [ROOT / "compose.yaml", ROOT / ".github/workflows/ci.yml"]:
        images = re.findall(r"^\s*image:\s*(\S+)", f.read_text(), re.M)
        assert images, f
        assert [i for i in images if i not in LOCAL_IMAGES and "@sha256:" not in i] == [], f.name
    # the CI MinIO container is started by `docker run`
    runs = re.findall(r"(cgr\.dev/\S+|docker\.io/\S+|ghcr\.io/\S+)", (ROOT / ".github/workflows/ci.yml").read_text())
    assert all("@sha256:" in r for r in runs), runs


def test_github_actions_are_pinned_by_commit_sha() -> None:
    uses = re.findall(r"uses:\s*(\S+)", (ROOT / ".github/workflows/ci.yml").read_text())
    assert uses
    assert [u for u in uses if not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u)] == []


def test_ocr_build_is_vendored_and_offline() -> None:
    d = (ROOT / "services/ocr/Dockerfile").read_text()
    pip_steps = [b for b in d.split("\nRUN ") if "pip install" in b]
    assert len(pip_steps) == 1 and pip_steps[0].startswith("--network=none") and "--no-index" in pip_steps[0]
    sums = (ROOT / "services/ocr/vendor.sha256").read_text().split()
    assert sums[1::2] == ["ocr-wheelhouse.tar", "ocr-models.tar"] and all(len(h) == 64 for h in sums[0::2])
