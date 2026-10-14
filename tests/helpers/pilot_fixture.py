"""Fixture for the tests of scripts/pilot/*: a scratch repository root with the real pilot scripts and stub helper scripts, and a
PATH whose docker/curl/openssl/crontab/systemctl are the logging stand-in in tests/helpers/fakebin."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FAKE = ROOT / "tests" / "helpers" / "fakebin" / "fake"
STUBS = ("compose_wait.sh", "fetch_ocr_vendor.sh", "fetch_age.sh", "backup_rotate.sh")


class World:
    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "repo"
        self.home = tmp / "home"
        self.bin = tmp / "bin"
        self.log = tmp / "calls.jsonl"
        self.rules_file = tmp / "rules.json"
        self.cron = tmp / "crontab.state"
        self.units = tmp / "units"
        (self.root / "scripts").mkdir(parents=True)
        self.home.mkdir()
        self.bin.mkdir()
        shutil.copytree(ROOT / "scripts" / "pilot", self.root / "scripts" / "pilot")
        for s in STUBS:  # the real ones need Docker; these only record that they were asked to run
            p = self.root / "scripts" / s
            p.write_text(
                '#!/bin/sh\necho "$0 $@" >> "$FAKE_LOG"\n'
                + (
                    'echo "BACKUP_ENV $GRADEMIND_BACKUP_RECIPIENTS $GRADEMIND_OFFSITE_DIR "'
                    '"$GRADEMIND_KEEP_DAILY $GRADEMIND_KEEP_WEEKLY"\n'
                    if s == "backup_rotate.sh"
                    else ""
                )
            )
            p.chmod(0o755)
        (self.root / "compose.yaml").write_text("services: {}\n")
        (self.root / "compose.https.yaml").write_text("services: {}\n")
        for n in ("docker", "curl", "openssl", "crontab", "systemctl"):
            (self.bin / n).symlink_to(FAKE)
        self.rules: list[dict[str, Any]] = []
        self.save_rules()

    def save_rules(self) -> None:
        self.rules_file.write_text(json.dumps(self.rules))

    def rule(self, **r: Any) -> None:
        self.rules.insert(0, r)
        self.save_rules()

    def env(self, **extra: str) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items() if not k.startswith(("PILOT_", "GRADEMIND_"))}
        e.update(
            PATH=f"{self.bin}:{e['PATH']}",
            HOME=str(self.home),
            PILOT_ROOT=str(self.root),
            FAKE_LOG=str(self.log),
            FAKE_RULES=str(self.rules_file),
            FAKE_CRON=str(self.cron),
            PILOT_UNIT_DIR=str(self.units),
        )
        e.update(extra)
        return e

    def run(self, script: str, *args: str, stdin: str = "", **env: str) -> subprocess.CompletedProcess[str]:
        cmd = [str(self.root / "scripts" / "pilot" / script), *args]
        if script.endswith(".py"):
            cmd = ["python3", *cmd]
        return subprocess.run(
            cmd, cwd=self.root, env=self.env(**env), capture_output=True, text=True, input=stdin, timeout=120, check=False
        )

    def calls(self, cmd: str | None = None) -> list[str]:
        if not self.log.exists():
            return []
        rows = []
        for x in self.log.read_text().splitlines():
            try:
                rows.append(json.loads(x))
            except ValueError:  # a stub script's plain line
                rows.append({"cmd": "script", "args": [x]})
        return [f"{r['cmd']} {' '.join(r['args'])}" for r in rows if cmd is None or r["cmd"] == cmd]

    def write_env(self, mode: int = 0o600, **over: str) -> Path:
        vals = {
            "GRADEMIND_JWT_SECRET": "j" * 40,
            "POSTGRES_PASSWORD": "p" * 20,
            "MINIO_ROOT_USER": "grademind",
            "MINIO_ROOT_PASSWORD": "m" * 20,
            "GRADEMIND_ADMIN_ORG": "Test College",
            "GRADEMIND_ADMIN_EMAIL": "admin@example.edu",
            "GRADEMIND_ADMIN_PASSWORD": "a" * 18,
            "GRADEMIND_HOST": "grademind.lan",
            "GRADEMIND_COOKIE_SECURE": "true",
            "GRADEMIND_S3_PUBLIC_ENDPOINT_URL": "https://grademind.lan:9443",
            "GRADEMIND_HTTPS_BIND": "192.168.1.20",
        }
        vals.update(over)
        p = self.root / ".env"
        p.write_text("".join(f"{k}={v}\n" for k, v in vals.items()))
        p.chmod(mode)
        return p
