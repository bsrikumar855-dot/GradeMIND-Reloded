"""OCR on its own queue: automatic retry bookkeeping and one active reading per booklet (Phase 4 step 4.0; D29)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10 15:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("processing_jobs", sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("processing_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    # Existing ocr_retry jobs (Phase 3) that are still active could already collide; there is at most one per booklet by the
    # API's own check, and the OCR stage was inside the ingest job before this step, so the index can be created directly.
    op.create_index(
        "uq_processing_jobs_active_ocr",
        "processing_jobs",
        ["submission_id"],
        unique=True,
        postgresql_where=sa.text("kind IN ('ocr', 'ocr_retry') AND status IN ('QUEUED', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_processing_jobs_active_ocr", table_name="processing_jobs")
    op.drop_column("processing_jobs", "next_attempt_at")
    op.drop_column("processing_jobs", "retry_count")
