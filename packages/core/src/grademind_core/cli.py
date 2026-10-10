"""Operator commands (used by docker compose one-shot services). Secrets come from the environment, never arguments.

python -m grademind_core.cli ensure-bucket
python -m grademind_core.cli create-admin --org "College" --email admin@college.edu   (password: GRADEMIND_ADMIN_PASSWORD)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from grademind_core.backup import read_archive, table_counts, verify_restore, write_archive
from grademind_core.config import get_settings
from grademind_core.dataset_export import SCOPES, ExportRefusedError, export_corrections
from grademind_core.db.models import AuditLog, Organization, Role, User
from grademind_core.db.session import session_factory
from grademind_core.result_snapshots import verify_all
from grademind_core.security import hash_password
from grademind_core.storage import ObjectStore


def ensure_bucket(wait_s: float = 60) -> int:
    store = ObjectStore(get_settings())
    deadline = time.monotonic() + wait_s
    while True:
        try:
            store.ensure_bucket()
            print("bucket ready")
            return 0
        except Exception as e:  # noqa: BLE001 - storage may still be starting
            if time.monotonic() > deadline:
                print(f"storage not reachable: {type(e).__name__}", file=sys.stderr)
                return 1
            time.sleep(1)


def create_admin(org: str, email: str) -> int:
    password = os.environ.get("GRADEMIND_ADMIN_PASSWORD", "")
    if len(password) < 12:
        print("GRADEMIND_ADMIN_PASSWORD must be set (>= 12 characters)", file=sys.stderr)
        return 2
    with session_factory(get_settings().database_url)() as s:
        if s.scalar(select(User).where(User.email == email.strip().lower())) is not None:
            print("admin already exists; nothing changed")
            return 0
        o = s.scalar(select(Organization).where(Organization.name == org)) or Organization(name=org)
        s.add(o)
        s.flush()
        u = User(org_id=o.id, email=email, display_name="Administrator", password_hash=hash_password(password), role=Role.ADMIN)
        s.add(u)
        s.flush()
        s.add(AuditLog(actor_id=None, action="user.bootstrap_admin", entity_type="user", entity_id=str(u.id)))
        s.commit()
    print("admin created")
    return 0


def create_user(org: str, email: str, role: str, name: str) -> int:
    """Add a user to an EXISTING organisation (used by the E2E to get a real examiner; admins have no user UI yet)."""
    password = os.environ.get("GRADEMIND_USER_PASSWORD", "")
    if len(password) < 12:
        print("GRADEMIND_USER_PASSWORD must be set (>= 12 characters)", file=sys.stderr)
        return 2
    with session_factory(get_settings().database_url)() as s:
        o = s.scalar(select(Organization).where(Organization.name == org))
        if o is None:
            print(f"organisation {org!r} does not exist", file=sys.stderr)
            return 2
        if s.scalar(select(User).where(User.email == email.strip().lower())) is not None:
            print("user already exists; nothing changed")
            return 0
        u = User(org_id=o.id, email=email, display_name=name, password_hash=hash_password(password), role=Role(role))
        s.add(u)
        s.flush()
        s.add(AuditLog(actor_id=None, action="user.cli_create", entity_type="user", entity_id=str(u.id), details={"role": role}))
        s.commit()
    print(f"{role} created")
    return 0


def verify_snapshots_cmd(exam: str | None, submission: str | None) -> int:
    """Recompute every finalized result from the raw records and compare (I12). Exit codes: 0 every snapshot reproduces exactly,
    1 at least one differs (each difference is printed), 2 the command could not run."""
    try:
        exam_id = uuid.UUID(exam) if exam else None
        sub_id = uuid.UUID(submission) if submission else None
    except ValueError:
        print("--exam and --submission must be ids", file=sys.stderr)
        return 2
    with session_factory(get_settings().database_url)() as s:
        report = verify_all(s, exam_id, sub_id)
    if report.ok:
        print(f"VERIFY OK: {report.checked} snapshot(s) recomputed from the raw records; every one reproduces exactly")
        return 0
    for key, problems in report.failures.items():
        print(f"VERIFY FAILED for booklet/snapshot {key}:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
    print(f"VERIFY FAILED: {len(report.failures)} of {report.checked} snapshot(s) do not reproduce", file=sys.stderr)
    return 1


def _settings_for(database: str | None, bucket: str | None):  # type: ignore[no-untyped-def]
    """The settings with the database NAME and/or the bucket replaced (a restore drill works on a side copy)."""
    from urllib.parse import urlsplit, urlunsplit

    s = get_settings()
    updates: dict[str, object] = {}
    if database:
        u = urlsplit(s.database_url)
        updates["database_url"] = urlunsplit((u.scheme, u.netloc, "/" + database, u.query, u.fragment))
    if bucket:
        updates["s3_bucket"] = bucket
    return s.model_copy(update=updates)


def backup_objects_cmd(bucket: str | None) -> int:
    """Stream every object of the bucket as a tar archive on stdout (progress goes to stderr)."""
    settings = _settings_for(None, bucket)
    manifest = write_archive(ObjectStore(settings), sys.stdout.buffer)
    sys.stdout.buffer.flush()
    print(f"backed up {manifest['count']} object(s), {manifest['bytes']} bytes", file=sys.stderr)
    return 0


def restore_objects_cmd(bucket: str | None) -> int:
    """Read a tar archive from stdin, verify it, and store its objects in the (new) bucket. Exit 1 if anything is wrong."""
    store = ObjectStore(_settings_for(None, bucket))
    store.ensure_bucket()
    rep = read_archive(store, sys.stdin.buffer)
    for p in rep.problems:
        print(f"RESTORE PROBLEM: {p}", file=sys.stderr)
    print(f"restored {rep.restored} object(s)" + ("" if rep.ok else f"; {len(rep.problems)} problem(s)"), file=sys.stderr)
    return 0 if rep.ok else 1


def count_rows_cmd(database: str | None) -> int:
    """Print the row counts of the main tables as JSON (the restore drill compares them before and after)."""
    import json

    with session_factory(_settings_for(database, None).database_url)() as s:
        print(json.dumps(table_counts(s), sort_keys=True))
    return 0


def verify_restore_cmd(database: str | None, bucket: str | None, compare: str | None) -> int:
    """Check a restored database + bucket against each other (revision, object hashes, snapshots). Exit 0 or 1."""
    import json

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    settings = _settings_for(database, bucket)
    here = Path(__file__).resolve().parents[2] / "alembic.ini"
    head = ScriptDirectory.from_config(Config(str(here))).get_current_head()
    before = json.loads(compare) if compare else None
    with session_factory(settings.database_url)() as s:
        rep = verify_restore(s, ObjectStore(settings), head or "", before)
    for p in rep.problems:
        print(f"RESTORE VERIFY PROBLEM: {p}", file=sys.stderr)
    if not rep.ok:
        print(f"RESTORE VERIFY FAILED: {len(rep.problems)} problem(s)", file=sys.stderr)
        return 1
    print(
        f"RESTORE VERIFY OK: schema {rep.revision}; {rep.objects_checked} stored object(s) match their recorded SHA-256; "
        f"{rep.snapshots_checked} result snapshot(s) recompute exactly; rows {json.dumps(rep.counts, sort_keys=True)}"
    )
    return 0


def export_corrections_cmd(admin: str, scope: str, out: str, exam: str | None, include_history: bool) -> int:
    """Owner-only dataset export (3.5). Exit codes: 0 complete, 2 refused (nothing written), 3 written, some rows skipped."""
    settings = get_settings()
    try:
        res = export_corrections(
            session_factory(settings.database_url),
            ObjectStore(settings),
            Path(out),
            scope,
            admin,
            uuid.UUID(exam) if exam else None,
            include_history,
        )
    except ExportRefusedError as e:
        print(f"refused: {e.message}", file=sys.stderr)
        return 2
    print(f"{scope} export: {res.rows} correction(s) written to {out}; {len(res.skipped)} skipped")
    print(res.manifest["warning"])
    for sk in res.skipped:
        print(f"  skipped {sk['correction_id']}: {sk['reason']}", file=sys.stderr)
    return 0 if res.ok else 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grademind_core.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ensure-bucket")
    ca = sub.add_parser("create-admin")
    ca.add_argument("--org", required=True)
    ca.add_argument("--email", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("--org", required=True)
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", required=True, choices=[r.value for r in Role])
    cu.add_argument("--name", default="User")
    vs = sub.add_parser("verify-snapshots", help="recompute every finalized result from the raw records (I12)")
    vs.add_argument("--exam", help="only this exam id")
    vs.add_argument("--submission", help="only this booklet id")
    sub.add_parser("backup-objects", help="stream every stored object as a tar archive on stdout").add_argument("--bucket")
    ro = sub.add_parser("restore-objects", help="read a backup tar from stdin into a (new) bucket")
    ro.add_argument("--bucket")
    sub.add_parser("count-rows", help="row counts of the main tables, as JSON").add_argument("--database")
    vr = sub.add_parser("verify-restore", help="check a restored database + bucket (revision, object hashes, snapshots)")
    vr.add_argument("--database")
    vr.add_argument("--bucket")
    vr.add_argument("--compare-counts", help="JSON from count-rows taken before the backup")
    ex = sub.add_parser("export-corrections")
    ex.add_argument("--as-admin", required=True, help="email of an active administrator (recorded in the audit log)")
    ex.add_argument("--scope", required=True, choices=SCOPES)
    ex.add_argument("--out", required=True, help="a NEW directory outside the git repository, or inside a git-ignored one")
    ex.add_argument("--exam", help="only this exam id")
    ex.add_argument("--include-history", action="store_true", help="every correction, not just the current one per line")
    a = ap.parse_args(argv)
    if a.cmd == "export-corrections":
        return export_corrections_cmd(a.as_admin, a.scope, a.out, a.exam, a.include_history)
    if a.cmd == "backup-objects":
        return backup_objects_cmd(a.bucket)
    if a.cmd == "restore-objects":
        return restore_objects_cmd(a.bucket)
    if a.cmd == "count-rows":
        return count_rows_cmd(a.database)
    if a.cmd == "verify-restore":
        return verify_restore_cmd(a.database, a.bucket, a.compare_counts)
    if a.cmd == "verify-snapshots":
        return verify_snapshots_cmd(a.exam, a.submission)
    if a.cmd == "ensure-bucket":
        return ensure_bucket()
    if a.cmd == "create-user":
        return create_user(a.org, a.email, a.role, a.name)
    return create_admin(a.org, a.email)


if __name__ == "__main__":
    sys.exit(main())
