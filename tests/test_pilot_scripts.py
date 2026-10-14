"""D31 pilot automation (scripts/pilot/*.sh): setup, schedule, backup job, health check, preflight, report.
The scripts run for real under bash; only the programs they call (docker, curl, openssl, crontab, systemctl) are recording
stand-ins. NOT covered here: a real Docker host (CI job `pilot-automation` runs them against a real stack)."""

from __future__ import annotations

import hashlib
import re
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pilot_fixture import World

PUBKEY = "age1" + "q" * 58
PRIVATE = "AGE-SECRET-" + "KEY-1" + "Z" * 40  # built in two parts: no key-looking literal sits in this repository


@pytest.fixture
def w(tmp_path: Path) -> World:
    return World(tmp_path)


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# ------------------------------------------------------------------------------------------------------------ pilot_setup.sh

SETUP = (
    "pilot_setup.sh",
    "--host",
    "grademind.lan",
    "--bind",
    "192.168.1.20",
    "--org",
    "Test College",
    "--admin-email",
    "Admin@Example.edu",
)


def good_setup_rules(w: World) -> None:
    w.rule(
        cmd="docker", match=r"compose cp proxy:", write_last_arg="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    )
    w.rule(cmd="curl", match=r"login", out="200")
    w.rule(cmd="openssl", match=r"fingerprint", out="sha256 Fingerprint=AA:BB:CC\n")


def secrets_of(env: Path) -> list[str]:
    keys = ("GRADEMIND_JWT_SECRET", "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD", "GRADEMIND_ADMIN_PASSWORD")
    vals = dict(ln.split("=", 1) for ln in env.read_text().splitlines() if "=" in ln and not ln.startswith("#"))
    return [vals[k] for k in keys]


def test_setup_dry_run_changes_nothing_and_says_what_it_would_do(w: World) -> None:
    r = w.run(*SETUP, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert (
        not (w.root / ".env").exists() and not (w.root / "pilot-data").exists() and w.calls() == []
    )  # nothing written, nothing called
    for part in (
        "would write .env (mode 600)",
        "GRADEMIND_COOKIE_SECURE=true",
        "https://grademind.lan:9443",
        "fetch_ocr_vendor.sh",
        "compose -f compose.yaml -f compose.https.yaml --profile https up -d --build",
        "compose_wait.sh",
        "root.crt",
        "DRY RUN finished",
    ):
        assert part in r.stdout, part


def test_setup_refuses_to_overwrite_an_existing_env(w: World) -> None:
    env = w.write_env()
    before = env.read_bytes()
    r = w.run(*SETUP)
    assert r.returncode == 2 and "not overwriting" in r.stderr and "--force" in r.stderr and env.read_bytes() == before
    assert w.calls() == []  # it stopped before touching Docker


def test_setup_writes_a_private_env_with_random_secrets_never_printed_and_starts_the_https_stack(w: World) -> None:
    good_setup_rules(w)
    r = w.run(*SETUP)
    assert r.returncode == 0, r.stdout + r.stderr
    env = w.root / ".env"
    assert mode(env) == 0o600
    vals = dict(ln.split("=", 1) for ln in env.read_text().splitlines() if "=" in ln and not ln.startswith("#"))
    assert vals["GRADEMIND_HOST"] == "grademind.lan" and vals["GRADEMIND_COOKIE_SECURE"] == "true"
    assert (
        vals["GRADEMIND_S3_PUBLIC_ENDPOINT_URL"] == "https://grademind.lan:9443"
        and vals["GRADEMIND_HTTPS_BIND"] == "192.168.1.20"
    )
    assert vals["GRADEMIND_ADMIN_EMAIL"] == "admin@example.edu" and vals["GRADEMIND_ADMIN_ORG"] == "Test College"
    sec = secrets_of(env)
    assert len(set(sec)) == 4 and all(len(s) >= 18 for s in sec)  # distinct and long
    assert not any(s in r.stdout + r.stderr for s in sec)  # nothing secret on screen
    assert "PRIVATE" not in env.read_text() and PRIVATE not in env.read_text()
    calls = w.calls()
    assert any("compose -f compose.yaml -f compose.https.yaml --profile https up -d --build" in c for c in calls)
    assert any("fetch_ocr_vendor.sh" in c for c in calls) and any("compose_wait.sh" in c for c in calls)
    assert (w.root / "pilot-data" / "ca" / "root.crt").read_text().startswith("-----BEGIN CERTIFICATE-----")
    assert "AA:BB:CC" in r.stdout and "Windows" in r.stdout and "iPhone" in r.stdout and "not printed here on purpose" in r.stdout
    assert "age-keygen" in r.stdout and "ANOTHER machine" in r.stdout  # the key pair is made elsewhere; this script makes none


def test_setup_force_keeps_the_old_env_private_and_replaces_the_secrets(w: World) -> None:
    good_setup_rules(w)
    old = w.write_env(mode=0o644)
    old_secrets = secrets_of(old)
    r = w.run(*SETUP, "--force")
    assert r.returncode == 0, r.stderr
    (bak,) = list(w.root.glob(".env.bak.*"))
    assert mode(bak) == 0o600 and secrets_of(bak) == old_secrets  # the copy is private and is the old one
    assert mode(w.root / ".env") == 0o600 and not set(secrets_of(w.root / ".env")) & set(old_secrets)


def test_setup_keep_env_is_repeatable_and_leaves_the_env_alone(w: World) -> None:
    good_setup_rules(w)
    env = w.write_env()
    h = hashlib.sha256(env.read_bytes()).hexdigest()
    for _ in range(2):
        r = w.run("pilot_setup.sh", "--keep-env")
        assert r.returncode == 0, r.stderr
        assert hashlib.sha256(env.read_bytes()).hexdigest() == h and not list(w.root.glob(".env.bak.*"))
    w.write_env(GRADEMIND_COOKIE_SECURE="false")
    assert w.run("pilot_setup.sh", "--keep-env").returncode == 2  # not an HTTPS setup: say so, do not start


@pytest.mark.parametrize(
    ("args", "msg"),
    [
        (("--host", "bad host!", "--bind", "10.0.0.1", "--org", "X", "--admin-email", "a@b.edu"), "not a host name"),
        (("--host", "h.lan", "--bind", "not-an-ip", "--org", "X", "--admin-email", "a@b.edu"), "IPv4"),
        (("--host", "h.lan", "--bind", "10.0.0.1", "--org", "X", "--admin-email", "nope"), "email"),
        (("--host", "h.lan", "--bind", "10.0.0.1", "--org", "A=B", "--admin-email", "a@b.edu"), "--org"),
    ],
)
def test_setup_rejects_bad_input_before_doing_anything(w: World, args: tuple[str, ...], msg: str) -> None:
    r = w.run("pilot_setup.sh", *args, "--dry-run")
    assert r.returncode == 2 and msg in r.stderr and w.calls() == [] and not (w.root / ".env").exists()


def test_setup_without_flags_and_without_a_person_to_ask_stops(w: World) -> None:
    r = w.run("pilot_setup.sh", "--yes")
    assert r.returncode == 2 and "give it with a flag" in r.stderr


def test_setup_fails_loudly_when_https_does_not_answer(w: World) -> None:
    w.rule(cmd="docker", match=r"compose cp proxy:", write_last_arg="CERT")
    w.rule(cmd="curl", match=r"login", out="502")
    r = w.run(*SETUP)
    assert r.returncode == 2 and "answered '502'" in r.stderr and "docker compose logs proxy" in r.stderr


def test_setup_refuses_an_env_that_holds_a_private_key(w: World) -> None:
    w.write_env()
    with (w.root / ".env").open("a") as fh:
        fh.write(f"X={PRIVATE}\n")
    r = w.run("pilot_setup.sh", "--keep-env")
    assert r.returncode == 2 and "PRIVATE key" in r.stderr


# ------------------------------------------------------------------------------------------------------------ pilot_schedule.sh


def recip(w: World, text: str = PUBKEY + "\n") -> Path:
    p = w.home / "recipients.txt"
    p.write_text(text)
    return p


def sched(w: World, *extra: str, **kw: str):  # type: ignore[no-untyped-def]
    off = w.home / "offsite"
    return w.run(
        "pilot_schedule.sh",
        "install",
        "--recipients",
        str(recip(w)),
        "--offsite",
        str(off),
        "--backup-dir",
        str(w.home / "bk"),
        "--allow-same-device",
        *extra,
        **kw,
    )


def test_schedule_installs_cron_entries_once_and_keeps_other_lines(w: World) -> None:
    w.cron.write_text("MAILTO=someone\n0 1 * * * /bin/other\n")
    r = sched(w)
    assert r.returncode == 0, r.stderr
    txt = w.cron.read_text()
    assert "0 1 * * * /bin/other" in txt and "MAILTO=someone" in txt  # the owner's own lines survive
    assert f"17 2 * * * cd {w.root} && ./scripts/pilot/pilot_backup_job.sh" in txt
    assert f"30 7 * * * cd {w.root} && ./scripts/pilot/pilot_health.sh" in txt
    sched(w, "--backup-time", "03:05", "--health-time", "06:45")  # again, with new times: replaced, not duplicated
    txt = w.cron.read_text()
    assert (
        txt.count("BEGIN grademind-pilot") == 1
        and txt.count("pilot_backup_job.sh") == 1
        and "5 3 * * *" in txt
        and "45 6 * * *" in txt
        and "17 2" not in txt
    )
    conf = w.root / "pilot-data" / "schedule.env"
    assert mode(conf) == 0o600
    c = conf.read_text()
    assert (
        f"GRADEMIND_BACKUP_RECIPIENTS={w.home / 'recipients.txt'}" in c
        and "GRADEMIND_KEEP_DAILY=14" in c
        and "GRADEMIND_KEEP_WEEKLY=8" in c
    )
    assert PRIVATE not in c and "AGE-SECRET" not in c
    assert any("fetch_age.sh" in x for x in w.calls())
    assert w.run("pilot_schedule.sh", "status").returncode == 0
    u = w.run("pilot_schedule.sh", "uninstall")
    assert (
        u.returncode == 0 and "BEGIN grademind-pilot" not in w.cron.read_text() and "0 1 * * * /bin/other" in w.cron.read_text()
    )
    assert w.run("pilot_schedule.sh", "status").returncode == 1


def test_schedule_refuses_a_private_key_and_a_file_without_a_public_key(w: World) -> None:
    r = w.run("pilot_schedule.sh", "install", "--recipients", str(recip(w, PRIVATE + "\n")), "--offsite", str(w.home / "o"))
    assert (
        r.returncode == 2
        and "PRIVATE key" in r.stderr
        and "another machine" in r.stderr
        and not (w.home / "o").exists()
        and not w.cron.exists()
    )
    r = w.run("pilot_schedule.sh", "install", "--recipients", str(recip(w, "nothing here\n")), "--offsite", str(w.home / "o"))
    assert r.returncode == 2 and "no public key line" in r.stderr
    both = recip(w, PUBKEY + "\n" + PRIVATE + "\n")  # even next to a good public line
    assert w.run("pilot_schedule.sh", "install", "--recipients", str(both), "--offsite", str(w.home / "o")).returncode == 2


def test_schedule_refuses_an_off_machine_folder_on_the_same_disk(w: World) -> None:
    r = w.run(
        "pilot_schedule.sh",
        "install",
        "--recipients",
        str(recip(w)),
        "--offsite",
        str(w.home / "off"),
        "--backup-dir",
        str(w.home / "bk"),
    )
    assert r.returncode == 2 and "same disk" in r.stderr and not w.cron.exists()


def test_schedule_dry_run_and_bad_times(w: World) -> None:
    r = sched(w, "--dry-run")
    assert r.returncode == 0 and "DRY-RUN would put this in your crontab" in r.stdout and "17 2 * * *" in r.stdout
    assert not w.cron.exists() and not (w.root / "pilot-data").exists() and w.calls("crontab") == []
    assert sched(w, "--backup-time", "25:00").returncode == 2


def test_schedule_with_systemd_writes_units_and_enables_the_timers(w: World) -> None:
    r = sched(w, "--systemd")
    assert r.returncode == 0, r.stderr
    t = (w.units / "grademind-backup.timer").read_text()
    assert "OnCalendar=*-*-* 02:17:00" in t and "Persistent=true" in t
    assert f"ExecStart={w.root}/scripts/pilot/pilot_backup_job.sh" in (w.units / "grademind-backup.service").read_text()
    assert f"ExecStart={w.root}/scripts/pilot/pilot_health.sh" in (w.units / "grademind-health.service").read_text()
    assert (
        any("--user enable --now grademind-backup.timer grademind-health.timer" in c for c in w.calls("systemctl"))
        and not w.cron.exists()
    )
    assert w.run("pilot_schedule.sh", "uninstall", "--systemd").returncode == 0 and not list(w.units.glob("grademind-*"))


# ------------------------------------------------------------------------------------------------------------ pilot_backup_job.sh


def test_the_backup_job_runs_backup_rotate_with_the_scheduled_settings_and_logs_it(w: World) -> None:
    assert sched(w).returncode == 0
    r = w.run("pilot_backup_job.sh")
    assert r.returncode == 0, r.stderr
    log = (w.root / "pilot-data" / "backup.log").read_text()
    assert "backup job" in log and f"BACKUP_ENV {w.home / 'recipients.txt'} {w.home / 'offsite'} 14 8" in log
    assert any("backup_rotate.sh" in c for c in w.calls())
    (w.root / "pilot-data" / "schedule.env").unlink()
    assert w.run("pilot_backup_job.sh").returncode == 2


# ------------------------------------------------------------------------------------------------------------ pilot_health.sh


def healthy(w: World, *, queue: str = "0", verify_rc: int = 0) -> None:
    w.rule(cmd="docker", match=r"redis-cli llen ocr", out=queue + "\n")
    w.rule(cmd="docker", match=r"ps --format \{\{\.Health\}\}", out="healthy\n")
    w.rule(
        cmd="docker",
        match=r"verify-snapshots",
        out="VERIFY OK: 3 snapshot(s)\n" if verify_rc == 0 else "VERIFY FAILED\n",
        rc=verify_rc,
    )


def backup_dir(w: World, age_h: float | None) -> Path:
    bk = w.root / "backups"
    (bk / "daily").mkdir(parents=True, exist_ok=True)
    if age_h is not None:
        (bk / "daily" / (datetime.now(UTC) - timedelta(hours=age_h)).strftime("%Y%m%dT%H%M%SZ")).mkdir(exist_ok=True)
    return bk


def health(w: World, *extra: str):  # type: ignore[no-untyped-def]
    return w.run("pilot_health.sh", "--min-free-pct", "0", "--min-free-gb", "0", *extra)


def test_health_ok_writes_exactly_one_line(w: World) -> None:
    w.write_env()
    healthy(w)
    backup_dir(w, 3)
    r = health(w)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = (w.root / "pilot-data" / "health.log").read_text().splitlines()
    assert (
        len(lines) == 1
        and lines[0] == r.stdout.strip()
        and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ OK services=healthy ocr_queue=0 backup_age_h=3 ", lines[0])
    )
    assert "verify=ok" in lines[0] and "proxy(" not in lines[0]
    health(w)
    assert len((w.root / "pilot-data" / "health.log").read_text().splitlines()) == 2  # one line per run


@pytest.mark.parametrize(
    ("what", "setup", "needle"),
    [
        (
            "service down",
            lambda w: w.rule(cmd="docker", match=r"ps --format \{\{\.Health\}\} ocr-worker", out="unhealthy\n"),
            "ocr-worker(unhealthy)",
        ),
        ("queue not empty", lambda w: w.rule(cmd="docker", match=r"redis-cli llen ocr", out="7\n"), "OCR queue has 7 waiting"),
        (
            "results differ",
            lambda w: w.rule(cmd="docker", match=r"verify-snapshots", out="VERIFY FAILED\n", rc=1),
            "verify-snapshots FAILED",
        ),
    ],
)
def test_health_fails_loudly_on_each_problem(w: World, what: str, setup, needle: str) -> None:  # type: ignore[no-untyped-def]
    w.write_env()
    healthy(w)
    backup_dir(w, 2)
    setup(w)
    r = health(w)
    assert r.returncode == 1, what
    last = (w.root / "pilot-data" / "health.log").read_text().splitlines()[-1]
    assert " FAIL " in last and needle in last


def test_health_backup_age_disk_and_missing_backup(w: World) -> None:
    w.write_env()
    healthy(w)
    backup_dir(w, 30)
    r = health(w)
    assert r.returncode == 1 and "newest backup is 30h old (limit 26h)" in r.stdout
    assert health(w, "--max-backup-age-h", "48").returncode == 0
    r = w.run("pilot_health.sh", "--max-backup-age-h", "48", "--min-free-gb", "99999999")
    assert r.returncode == 1 and "low disk space" in r.stdout
    for p in (w.root / "backups" / "daily").iterdir():
        p.rmdir()
    assert "no nightly backup found" in health(w).stdout


def test_health_includes_the_proxy_when_https_is_on_and_dry_run_writes_nothing(w: World) -> None:
    w.write_env()
    healthy(w)
    backup_dir(w, 1)
    w.rule(cmd="docker", match=r"ps --format \{\{\.Health\}\} proxy", out="starting\n")
    assert "proxy(starting)" in health(w).stdout
    log = w.root / "pilot-data" / "health.log"
    n = len(log.read_text().splitlines())
    r = w.run("pilot_health.sh", "--dry-run")
    assert r.returncode == 0 and "DRY-RUN would check" in r.stdout and len(log.read_text().splitlines()) == n


# ------------------------------------------------------------------------------------------------------------ pilot_report.sh


def test_report_shows_the_banner_before_the_numbers_and_never_the_text(w: World) -> None:
    w.write_env()
    w.rule(
        cmd="docker",
        match=r"cli pilot-cer",
        out="Machine reading, measured on 60 corrected line(s)\n  Character error rate: 12.0%\n",
    )
    r = w.run("pilot_report.sh")
    assert r.returncode == 0, r.stderr
    assert r.stdout.index("ERROR AMONG CORRECTED LINES ONLY, NOT SYSTEM ACCURACY") < r.stdout.index("Character error rate")
    assert "no transcribed sample found" in r.stdout
    assert [c for c in w.calls("docker") if "cli " in c] == ["docker compose exec -T api python -m grademind_core.cli pilot-cer"]


def test_report_refusal_gives_exit_2_and_a_sample_is_copied_in_run_and_removed(w: World) -> None:
    w.write_env()
    s = w.home / "pilot-samples" / "batchA"
    s.mkdir(parents=True)
    (s / "transcriptions.json").write_text('{"batch": "A", "lines": {"s001": {"text": "hello", "unreadable": false}}}')
    w.rule(cmd="docker", match=r"cli pilot-cer", err="refused: only 10 corrected line(s)\n", rc=2)
    w.rule(cmd="docker", match=r"pilot-sample-cer", out="Machine reading, measured on a random sample of 50 line(s)\n")
    r = w.run("pilot_report.sh")
    assert r.returncode == 2 and "refused: only 10" in r.stderr
    assert "RANDOM SAMPLE, TRANSCRIBED BY THE OWNER" in r.stdout and "random sample of 50" in r.stdout
    calls = w.calls("docker")
    cp = [i for i, c in enumerate(calls) if "compose cp" in c and "api:/tmp/pilot_sample_batchA" in c]
    run_ = [i for i, c in enumerate(calls) if "pilot-sample-cer /tmp/pilot_sample_batchA" in c]
    rm = [i for i, c in enumerate(calls) if c.endswith("rm -rf /tmp/pilot_sample_batchA")]
    assert cp and run_ and rm and cp[0] < run_[0] < rm[-1]  # in, run, out again
    assert w.run("pilot_report.sh", "--no-auto-samples").stdout.count("RANDOM SAMPLE") == 0
    d = w.run("pilot_report.sh", "--dry-run")
    assert d.returncode == 0 and "DRY-RUN would run" in d.stdout


# ------------------------------------------------------------------------------------------------------------ pilot_preflight.sh


def offsite_on_another_device() -> Path:
    dev = Path("/dev/shm")  # noqa: S108 - a tmpfs: a second device to tell apart from the test directory
    if not dev.is_dir() or dev.stat().st_dev == Path(tempfile.gettempdir()).stat().st_dev:
        pytest.skip("needs a second filesystem (/dev/shm) to tell two devices apart")
    return Path(tempfile.mkdtemp(dir=dev))


def good_world(w: World, offsite: Path) -> None:
    w.write_env()
    ca = w.root / "pilot-data" / "ca"
    ca.mkdir(parents=True, exist_ok=True)
    (ca / "root.crt").write_text("CERT")
    (w.home / "recipients.txt").write_text(PUBKEY + "\n")
    (w.root / "pilot-data" / "schedule.env").write_text(
        f"GRADEMIND_BACKUP_DIR={w.root / 'backups'}\nGRADEMIND_BACKUP_RECIPIENTS={w.home / 'recipients.txt'}\n"
        f"GRADEMIND_OFFSITE_DIR={offsite}\n"
    )
    backup_dir(w, 4)
    w.cron.write_text("# BEGIN grademind-pilot (managed by scripts/pilot/pilot_schedule.sh; edit with that script)\n")
    (w.root / "pilot-data" / "health.log").write_text(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") + " OK services=healthy\n")
    (w.root / "pilot-data" / "restore-drill.txt").write_text(
        (datetime.now(UTC) - timedelta(days=9)).strftime("%F") + " recorded-by-owner x\n"
    )
    w.rule(cmd="systemctl", match=r"is-enabled", rc=1)  # (the schedule is the cron block above)
    w.rule(cmd="curl", match=r"login", out="HTTP/2 200\nstrict-transport-security: max-age=31536000\n")
    w.rule(cmd="docker", match=r"printenv GRADEMIND_COOKIE_SECURE", out="true\n")
    w.rule(
        cmd="docker", match=r"^ps --format", out="grademind-proxy-1 192.168.1.20:8443->8443/tcp, 192.168.1.20:9443->9443/tcp\n"
    )
    w.rule(cmd="docker", match=r"verify-snapshots", out="VERIFY OK: 3 snapshot(s) recomputed\n")
    w.rule(cmd="docker", match=r"role='admin'", out="1\n")
    w.rule(cmd="docker", match=r"role='examiner' and is_active\"?$", out="4\n")
    w.rule(cmd="docker", match=r"not exists", out="0\n")
    w.rule(cmd="docker", match=r"must_change_password", out="2\n")
    w.rule(cmd="docker", match=r"consent_scope", out="local_only: 5\n")


def pre(w: World, *extra: str, **env: str):  # type: ignore[no-untyped-def]
    return w.run("pilot_preflight.sh", *extra, **env)


def test_preflight_all_pass_when_everything_is_in_place_and_manual_lines_are_never_ticked(w: World) -> None:
    good_world(w, offsite_on_another_device())
    r = pre(w)
    assert r.returncode == 0, r.stdout
    assert "FAIL" not in re.sub(r"SUMMARY.*", "", r.stdout)
    assert re.search(r"SUMMARY: \d+ PASS, 0 FAIL, [89] MANUAL", r.stdout)
    manual = [ln for ln in r.stdout.splitlines() if ln.startswith("MANUAL")]
    assert (
        any("walk-through" in m for m in manual)
        and any("D20" in m for m in manual)
        and any("read docs/WHAT_THIS_SYSTEM_DOES.md" in m for m in manual)
    )
    assert not any(
        ln.startswith("PASS") and ("walk-through" in ln or "D20" in ln or "WHAT_THIS_SYSTEM_DOES" in ln)
        for ln in r.stdout.splitlines()
    )
    assert (
        "booklets by data-use choice so far: local_only: 5" in r.stdout
        and "2 account(s) still hold a temporary password" in r.stdout
    )


@pytest.mark.parametrize(
    ("what", "break_it", "needle"),
    [
        ("env too open", lambda w: (w.root / ".env").chmod(0o644), ".env must be mode 600"),
        ("not secure cookies", lambda w: w.write_env(GRADEMIND_COOKIE_SECURE="false"), "GRADEMIND_COOKIE_SECURE is not true"),
        ("listens everywhere", lambda w: w.write_env(GRADEMIND_HTTPS_BIND="0.0.0." + "0"), "listens on every network"),
        ("https down", lambda w: w.rule(cmd="curl", match=r"login", out="HTTP/2 502\n"), "does not answer 200"),
        ("no hsts", lambda w: w.rule(cmd="curl", match=r"login", out="HTTP/2 200\n"), "no Strict-Transport-Security"),
        (
            "other ports published",
            lambda w: w.rule(cmd="docker", match=r"^ps --format", out="grademind-web-1 0.0.0.0:3100->3000/tcp\n"),
            "only the proxy should",
        ),
        (
            "results differ",
            lambda w: w.rule(cmd="docker", match=r"verify-snapshots", out="VERIFY FAILED\n"),
            "did not print VERIFY OK",
        ),
        (
            "no examiner",
            lambda w: w.rule(cmd="docker", match=r"role='examiner' and is_active\"?$", out="0\n"),
            "no active examiner",
        ),
        (
            "unassigned examiner",
            lambda w: w.rule(cmd="docker", match=r"not exists", out="2\n"),
            "2 active examiner(s) have no exam assigned",
        ),
        (
            "old backup",
            lambda w: [p.rename(p.with_name("20200101T000000Z")) for p in (w.root / "backups" / "daily").iterdir()],
            "h old (limit 26)",
        ),
        ("no schedule", lambda w: w.cron.unlink(), "not scheduled"),
        (
            "health stale or failed",
            lambda w: (w.root / "pilot-data" / "health.log").write_text("2020-01-01T00:00:00Z FAIL x\n"),
            "health check failed",
        ),
        ("no drill", lambda w: (w.root / "pilot-data" / "restore-drill.txt").unlink(), "no restore drill recorded"),
        (
            "old drill",
            lambda w: (w.root / "pilot-data" / "restore-drill.txt").write_text("2020-01-01 recorded-by-owner x\n"),
            "no restore drill recorded",
        ),
        ("no ca", lambda w: (w.root / "pilot-data" / "ca" / "root.crt").unlink(), "no CA root"),
    ],
)
def test_preflight_fails_each_machine_checkable_problem(w: World, what: str, break_it, needle: str) -> None:  # type: ignore[no-untyped-def]
    good_world(w, offsite_on_another_device())
    break_it(w)
    r = pre(w)
    assert r.returncode == 1, what
    assert any(ln.startswith("FAIL") and needle in ln for ln in r.stdout.splitlines()), r.stdout


def test_preflight_finds_a_private_key_anywhere_it_looks_and_a_same_device_offsite_folder(w: World) -> None:
    off = offsite_on_another_device()
    for where in (".env", "home/Downloads/k.txt", "repo/notes/key.txt", "home/recipients.txt"):
        good_world(w, off)
        target = w.root / ".env" if where == ".env" else w.root.parent / where
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as fh:
            fh.write(f"\n# {PRIVATE}\n")
        r = pre(w)
        assert r.returncode == 1 and any(
            ln.startswith("FAIL") and "PRIVATE key is on this host" in ln for ln in r.stdout.splitlines()
        ), where
        assert PRIVATE not in r.stdout  # it names the file, never the key
        if where.endswith(("k.txt", "key.txt")):
            target.unlink()
    good_world(w, w.home / "same-disk-offsite")
    (w.home / "same-disk-offsite").mkdir(exist_ok=True)
    r = pre(w)
    assert any(ln.startswith("FAIL") and "SAME device" in ln for ln in r.stdout.splitlines())


def test_preflight_dry_run_and_the_restore_drill_record(w: World) -> None:
    r = pre(w, "--dry-run")
    assert r.returncode == 0 and "DRY-RUN: this would check" in r.stdout and "MANUAL" in r.stdout and w.calls() == []
    assert pre(w, "--record-restore-drill", "--dry-run").returncode == 0 and not (w.root / "pilot-data").exists()
    r = pre(w, "--record-restore-drill", "2026-10-01")
    assert r.returncode == 0 and "your own record" in r.stdout
    assert (w.root / "pilot-data" / "restore-drill.txt").read_text().startswith("2026-10-01 recorded-by-owner")
    assert pre(w, "--record-restore-drill", "tomorrow").returncode == 2
    pre(w, "--record-restore-drill")
    assert (w.root / "pilot-data" / "restore-drill.txt").read_text().splitlines()[-1].startswith(datetime.now(UTC).strftime("%F"))


def test_preflight_checks_the_real_cookie_only_when_given_the_admin_login(w: World) -> None:
    good_world(w, offsite_on_another_device())
    assert "session cookie flags: set PILOT_ADMIN_EMAIL" in pre(w).stdout  # no password: MANUAL, not a guess
    w.rule(cmd="curl", match=r"api/session", out="HTTP/2 303\nset-cookie: gm_session=x; Path=/; HttpOnly; Secure; SameSite=lax\n")
    r = pre(w, PILOT_ADMIN_EMAIL="a@b.edu", PILOT_ADMIN_PASSWORD="whatever-long-password")
    assert (
        any(ln.startswith("PASS") and "Secure and HttpOnly" in ln for ln in r.stdout.splitlines())
        and "whatever-long-password" not in r.stdout + r.stderr
    )
    w.rule(cmd="curl", match=r"api/session", out="HTTP/2 303\nset-cookie: gm_session=x; Path=/\n")
    assert any(
        ln.startswith("FAIL") and "not Secure and HttpOnly" in ln
        for ln in pre(w, PILOT_ADMIN_EMAIL="a@b.edu", PILOT_ADMIN_PASSWORD="x").stdout.splitlines()
    )


def test_no_script_makes_or_stores_a_private_key_or_touches_consent_or_verification() -> None:
    root = Path(__file__).resolve().parents[1] / "scripts" / "pilot"
    for f in root.iterdir():
        if f.suffix not in (".sh", ".py"):
            continue
        t = f.read_text()
        assert (
            "age-keygen" not in t.replace("age-keygen)", "") or f.name == "pilot_setup.sh"
        )  # only setup MENTIONS it, to tell the owner to run it elsewhere
        assert not re.search(r"(?m)^\s*[^#\n]*(age-keygen\s+-o|OWNER_VERIFIED\s*=|consent_scope\s*=)", t), f.name
    setup = (root / "pilot_setup.sh").read_text()
    assert re.search(r"(?m)^\s*(run |)[^#\n]*age-keygen\b", setup) is None or "ANOTHER machine" in setup
