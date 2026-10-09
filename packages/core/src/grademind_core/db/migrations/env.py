from __future__ import annotations

import os

from alembic import context
from grademind_core.db.models import Base
from sqlalchemy import create_engine

_url = os.environ.get("GRADEMIND_DATABASE_URL") or context.config.get_main_option("sqlalchemy.url")
if not _url:
    raise SystemExit("set GRADEMIND_DATABASE_URL")
url: str = _url


def run() -> None:
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
