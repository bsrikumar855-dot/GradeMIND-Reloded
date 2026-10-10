"""Operator commands (used by docker compose one-shot services). Secrets come from the environment, never arguments.

python -m grademind_core.cli ensure-bucket
python -m grademind_core.cli create-admin --org "College" --email admin@college.edu   (password: GRADEMIND_ADMIN_PASSWORD)
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from sqlalchemy import select

from grademind_core.config import get_settings
from grademind_core.db.models import AuditLog, Organization, Role, User
from grademind_core.db.session import session_factory
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
    a = ap.parse_args(argv)
    if a.cmd == "ensure-bucket":
        return ensure_bucket()
    if a.cmd == "create-user":
        return create_user(a.org, a.email, a.role, a.name)
    return create_admin(a.org, a.email)


if __name__ == "__main__":
    sys.exit(main())
