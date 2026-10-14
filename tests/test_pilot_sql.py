"""The SQL that scripts/pilot/pilot_preflight.sh sends to Postgres must be valid against the real schema. The script tests use a
stand-in for docker and cannot catch this (the first real run did: an aggregate inside GROUP BY). Needs GRADEMIND_TEST_DATABASE_URL."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs GRADEMIND_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


def test_every_query_in_the_preflight_runs_against_the_current_schema() -> None:
    env = {**os.environ, "GRADEMIND_DATABASE_URL": URL or ""}
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "packages" / "core" / "alembic.ini"), "upgrade", "head"],
        env=env, check=True, capture_output=True,
    )  # fmt: skip
    queries = re.findall(r'psql_ "([^"]+)"', (ROOT / "scripts" / "pilot" / "pilot_preflight.sh").read_text())
    assert len(queries) >= 5, "the script's queries were not found"
    engine = create_engine(URL or "")
    with engine.connect() as c:
        for q in queries:
            c.execute(text(q.replace(":", "\\:"))).fetchall()  # ':' would read as a bind parameter
    engine.dispose()
