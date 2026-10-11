"""D26 / 5.3 (D30.5): every image in the stack is pinned by digest; the plain stack publishes ports on the host's loopback
only; with the HTTPS profile the reverse proxy is the ONLY service that publishes anything. Reads the merged compose
configuration (skipped when docker is not installed)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = {
    **os.environ,
    "POSTGRES_PASSWORD": "x",
    "MINIO_ROOT_USER": "u",
    "MINIO_ROOT_PASSWORD": "p",
    "GRADEMIND_JWT_SECRET": "s",
    "GRADEMIND_ADMIN_EMAIL": "a@b.in",
    "GRADEMIND_ADMIN_ORG": "Org",
    "GRADEMIND_ADMIN_PASSWORD": "long-enough-password",
    "GRADEMIND_HOST": "grademind.lan",
}
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI")


def merged(*files: str, profiles: tuple[str, ...] = ()) -> dict[str, Any]:
    cmd = [
        "docker",
        "compose",
        *[a for f in files for a in ("-f", f)],
        *[a for p in profiles for a in ("--profile", p)],
        "config",
        "--format",
        "json",
    ]
    out = subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, check=True).stdout
    cfg: dict[str, Any] = json.loads(out)
    return cfg


def published(cfg: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {name: svc["ports"] for name, svc in cfg["services"].items() if svc.get("ports")}


def test_every_third_party_image_is_pinned_by_digest() -> None:
    cfg = merged("compose.yaml", "compose.https.yaml", profiles=("https",))
    for name, svc in cfg["services"].items():
        image = svc.get("image", "")
        if "build" in svc or image.startswith(("grademind-app", "grademind-web", "grademind-ocr")):
            continue  # built here
        assert "@sha256:" in image, f"{name}: {image} is not pinned by digest"
    assert "caddy" in cfg["services"]["proxy"]["image"]


def test_the_plain_stack_publishes_only_on_the_hosts_loopback_and_has_no_proxy() -> None:
    cfg = merged("compose.yaml")
    assert "proxy" not in cfg["services"]
    for name, ports in published(cfg).items():
        assert all(p.get("host_ip") == "127.0.0.1" for p in ports), f"{name} publishes beyond loopback: {ports}"


def test_with_the_https_profile_only_the_proxy_publishes_a_port() -> None:
    cfg = merged("compose.yaml", "compose.https.yaml", profiles=("https",))
    pub = published(cfg)
    assert list(pub) == ["proxy"], f"services publishing ports: {list(pub)}"
    assert sorted(p["target"] for p in pub["proxy"]) == [8443, 9443]
    assert all(p["host_ip"] == "127.0.0.1" for p in pub["proxy"])  # a LAN address is a deliberate choice (GRADEMIND_HTTPS_BIND)
    assert cfg["services"]["proxy"]["profiles"] == ["https"]
