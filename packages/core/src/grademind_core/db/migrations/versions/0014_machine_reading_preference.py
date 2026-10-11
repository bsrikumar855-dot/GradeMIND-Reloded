"""Per-account "hide machine reading" preference, stored on the server (Phase 5 follow-up; D31.3)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-11 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("machine_reading_hidden", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "machine_reading_hidden")
