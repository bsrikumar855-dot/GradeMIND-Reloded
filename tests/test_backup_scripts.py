"""5.2 (D30.4): retention, and encryption of a backup with age. The age tests run the pinned binary (scripts/fetch_age.sh)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AGE = ROOT / "tools" / "age" / "age"
KEYGEN = ROOT / "tools" / "age" / "age-keygen"
needs_age = pytest.mark.skipif(not (AGE.exists() and KEYGEN.exists()), reason="needs tools/age (scripts/fetch_age.sh)")
SECRET_PREFIX = "AGE-SECRET-" + "KEY-"  # built in two parts so no key-looking literal sits in this repository


def run(
    *args: str | Path, cwd: Path = ROOT, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [str(a) for a in args], cwd=cwd, capture_output=True, text=True, check=check, env={**os.environ, **(env or {})}
    )


# ---------------------------------------------------------------------------------------------------- retention


def stamps(n: int, start_day: int = 1) -> list[str]:
    return [f"202610{start_day + i:02d}T020000Z" for i in range(n)]


def test_prune_keeps_the_newest_and_touches_nothing_else(tmp_path: Path) -> None:
    for s in stamps(20):
        (tmp_path / s).mkdir()
        (tmp_path / s / "db.dump").write_text("x")
    (tmp_path / "notes.txt").write_text("keep me")
    (tmp_path / "20261099-not-a-backup").mkdir()  # not a timestamp: never considered
    out = run(SCRIPTS / "backup_prune.sh", tmp_path, "14").stdout
    left = sorted(
        p.name for p in tmp_path.iterdir() if p.name.startswith("2026100") or p.name.startswith("202610T") or p.name[8:9] == "T"
    )
    assert left == stamps(20)[6:]  # the 14 newest
    assert out.count("removed") == 6 and "20261001T020000Z" in out and "20261007T020000Z" not in out
    assert (tmp_path / "notes.txt").read_text() == "keep me" and (tmp_path / "20261099-not-a-backup").is_dir()


def test_prune_weekly_encrypted_files_and_their_checksums(tmp_path: Path) -> None:
    for s in stamps(10):
        (tmp_path / f"{s}.tar.age").write_text("enc")
        (tmp_path / f"{s}.tar.age.sha256").write_text("sum")
    run(SCRIPTS / "backup_prune.sh", tmp_path, "8")
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted([f"{s}.tar.age" for s in stamps(10)[2:]] + [f"{s}.tar.age.sha256" for s in stamps(10)[2:]])


def test_prune_does_nothing_when_there_are_few_and_rejects_bad_limits(tmp_path: Path) -> None:
    for s in stamps(3):
        (tmp_path / s).mkdir()
    assert "keeping all" in run(SCRIPTS / "backup_prune.sh", tmp_path, "14").stdout
    assert len(list(tmp_path.iterdir())) == 3
    for bad in ("0", "-1", "x", ""):
        assert run(SCRIPTS / "backup_prune.sh", tmp_path, bad, check=False).returncode != 0
    assert run(SCRIPTS / "backup_prune.sh", tmp_path / "missing", "14", check=False).returncode == 2


# ---------------------------------------------------------------------------------------------------- encryption


def keypair(tmp_path: Path, name: str = "key") -> tuple[Path, Path]:
    ident = tmp_path / f"{name}.txt"
    res = run(KEYGEN, "-o", ident)
    pub = tmp_path / f"{name}.pub"
    pub.write_text(res.stderr.split("Public key:")[1].strip().splitlines()[0].strip() + "\n")
    return ident, pub


def make_backup(tmp_path: Path) -> Path:
    d = tmp_path / "20261011T020000Z"
    d.mkdir()
    (d / "db.dump").write_bytes(b"PGDMP-secret-student-rows-" + bytes(range(256)) * 20)
    (d / "objects.tar").write_bytes(b"objects" * 500)
    (d / "MANIFEST.txt").write_text("GradeMIND backup\nrow_counts: {}\n")
    return d


@needs_age
def test_an_encrypted_copy_holds_none_of_the_plaintext_and_only_the_owners_key_opens_it(tmp_path: Path) -> None:
    ident, pub = keypair(tmp_path, "owner")
    other, _ = keypair(tmp_path, "other")
    backup = make_backup(tmp_path)
    out = tmp_path / "weekly" / "20261011T020000Z.tar.age"
    out.parent.mkdir()
    run(SCRIPTS / "backup_encrypt.sh", backup, pub, out)
    blob = out.read_bytes()
    assert blob.startswith(b"age-encryption.org/v1")
    assert b"PGDMP" not in blob and b"GradeMIND backup" not in blob and b"secret-student-rows" not in blob
    # the owner's key restores exactly what was backed up
    unpack = tmp_path / "unpacked"
    inner = Path(run(SCRIPTS / "backup_decrypt.sh", out, ident, unpack).stdout.strip())
    assert inner.name == "20261011T020000Z"
    assert {p.name: p.read_bytes() for p in inner.iterdir()} == {p.name: p.read_bytes() for p in backup.iterdir()}
    # nobody else's key does, and a damaged file is refused (age authenticates what it decrypts)
    assert run(SCRIPTS / "backup_decrypt.sh", out, other, tmp_path / "u2", check=False).returncode != 0
    bad = bytearray(blob)
    bad[len(bad) // 2] ^= 0xFF
    broken = tmp_path / "broken.tar.age"
    broken.write_bytes(bytes(bad))
    assert run(SCRIPTS / "backup_decrypt.sh", broken, ident, tmp_path / "u3", check=False).returncode != 0


@needs_age
def test_encryption_refuses_a_private_key_a_missing_key_and_an_existing_output(tmp_path: Path) -> None:
    ident, pub = keypair(tmp_path)
    backup = make_backup(tmp_path)
    r = run(SCRIPTS / "backup_encrypt.sh", backup, ident, tmp_path / "a.tar.age", check=False)  # the PRIVATE key file
    assert r.returncode == 2 and "PRIVATE key" in r.stderr and not (tmp_path / "a.tar.age").exists()
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing here\n")
    assert run(SCRIPTS / "backup_encrypt.sh", backup, empty, tmp_path / "b.tar.age", check=False).returncode == 2
    out = tmp_path / "c.tar.age"
    run(SCRIPTS / "backup_encrypt.sh", backup, pub, out)
    assert run(SCRIPTS / "backup_encrypt.sh", backup, pub, out, check=False).returncode == 2  # never overwrites


@needs_age
def test_a_private_key_in_the_stack_environment_stops_the_nightly_job(tmp_path: Path) -> None:
    """The nightly job copies itself into a scratch repo layout so it reads a scratch .env, not the real one."""
    scratch = tmp_path / "repo"
    shutil.copytree(SCRIPTS, scratch / "scripts")
    (scratch / ".env").write_text(f"POSTGRES_PASSWORD=x\nBACKUP_KEY={SECRET_PREFIX}ABCDEF\n")
    r = run(scratch / "scripts" / "backup_rotate.sh", cwd=scratch, check=False)
    assert r.returncode == 2 and "PRIVATE key" in r.stderr
    (scratch / ".env").write_text("POSTGRES_PASSWORD=x\n")
    r = run(scratch / "scripts" / "backup_rotate.sh", "--weekly", cwd=scratch, check=False)  # weekly without a recipients file
    assert r.returncode == 2 and "GRADEMIND_BACKUP_RECIPIENTS" in r.stderr
