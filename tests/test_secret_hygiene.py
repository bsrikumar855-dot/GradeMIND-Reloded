"""D26.3: the repo is PUBLIC. No env file, key file or credential file may ever be tracked; .env must be ignored;
.env.example may hold placeholders only (the app refuses them outside tests)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git") or "git"

FORBIDDEN = re.compile(
    r"(^|/)("
    r"\.env(\..*)?"  # .env, .env.local, ...
    r"|.*\.(pem|key|p12|pfx|jks|keystore|ppk|kdbx)"
    r"|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?"
    r"|.*credentials.*\.json|.*service[-_]account.*\.json|\.netrc|\.pgpass|\.npmrc|\.pypirc"
    r"|pii_patterns\.txt"  # the local redaction pattern list names real people
    r")$"
)
ALLOWED = {".env.example"}


def tracked() -> list[str]:
    out = subprocess.run([GIT, "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout.decode()
    return [f for f in out.split("\0") if f]


def test_no_secret_files_are_tracked() -> None:
    files = tracked()
    assert len(files) > 50  # sanity: we are looking at the real repo
    assert [f for f in files if FORBIDDEN.search(f) and f not in ALLOWED] == []


def test_env_files_are_ignored() -> None:
    for name in [".env", ".env.local", "apps/web/.env", "services/ocr/.env.production"]:
        r = subprocess.run([GIT, "check-ignore", "-q", name], cwd=ROOT)
        assert r.returncode == 0, f"{name} would not be ignored"
    assert subprocess.run([GIT, "check-ignore", "-q", ".env.example"], cwd=ROOT).returncode == 1


def test_env_example_holds_placeholders_only() -> None:
    values = dict(
        line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines() if line and not line.startswith("#")
    )
    for key in ["GRADEMIND_JWT_SECRET", "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD", "GRADEMIND_ADMIN_PASSWORD"]:
        assert "change-me" in values[key], key


def test_the_pattern_catches_what_it_should() -> None:
    bad_names = [
        ".env",
        "apps/api/.env.prod",
        "keys/deploy.pem",
        "id_ed25519",
        "gcp-service-account-x.json",
        "x/aws_credentials.json",
    ]
    for bad in bad_names:
        assert FORBIDDEN.search(bad), bad
    for ok in [".env.example", "packages/core/src/grademind_core/security.py", "docs/key-decisions.md"]:
        assert not FORBIDDEN.search(ok) or ok in ALLOWED, ok
