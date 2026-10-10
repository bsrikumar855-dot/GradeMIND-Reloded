"""Finalize / reopen with frozen result snapshots (Phase 4 step 4.2; I8, I12)

Two append-only tables, and database-level guards:
- result_snapshots: a booklet's frozen result, with everything needed to recompute it from the raw records.
- finalization_events: FINALIZED / REOPENED per booklet. A trigger keeps them strictly alternating.
- While the latest event of a booklet is FINALIZED, INSERT into evaluations and score_results and any INSERT/UPDATE/DELETE of its
  answer_regions are refused by the database itself (the API checks first and answers 409; this is the backstop).
  The guard first takes FOR SHARE on the submission row; finalizing takes FOR UPDATE on it, so a grading write in flight and a
  finalize cannot interleave: the finalize waits for the write, or the write waits and is then refused.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-10 17:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

GUARDED = (
    ("evaluations", "INSERT"),
    ("score_results", "INSERT"),
    ("answer_regions", "INSERT OR UPDATE OR DELETE"),
)


def upgrade() -> None:
    op.create_table(
        "result_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=False),
        sa.Column("exam_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_no", sa.Integer(), nullable=False),
        sa.Column("paper_version_id", sa.UUID(), nullable=False),
        sa.Column("rubric_version_id", sa.UUID(), nullable=False),
        sa.Column("paper_version_no", sa.Integer(), nullable=False),
        sa.Column("rubric_version_no", sa.Integer(), nullable=False),
        sa.Column("score_computer_version", sa.String(length=40), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evaluation_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inputs_sha256", sa.String(length=64), nullable=False),
        sa.Column("total", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("max_total", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("sheet", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"]),
        sa.ForeignKeyConstraint(["exam_id"], ["exams.id"]),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"]),
        sa.ForeignKeyConstraint(["rubric_version_id"], ["rubric_versions.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq", name="uq_result_snapshots_seq"),
        sa.UniqueConstraint("submission_id", "snapshot_no", name="uq_result_snapshots_submission_no"),
    )
    op.create_index("ix_result_snapshots_submission_id", "result_snapshots", ["submission_id"])
    op.create_index("ix_result_snapshots_exam_id", "result_snapshots", ["exam_id"])
    op.create_table(
        "finalization_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["result_snapshots.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq", name="uq_finalization_events_seq"),
        sa.CheckConstraint("action IN ('FINALIZED', 'REOPENED')", name="ck_finalization_events_action"),
        sa.CheckConstraint(
            "action <> 'REOPENED' OR length(btrim(coalesce(reason, ''))) >= 10", name="ck_finalization_events_reopen_reason"
        ),
    )
    op.create_index("ix_finalization_events_submission_id", "finalization_events", ["submission_id"])

    for t in ("result_snapshots", "finalization_events"):
        op.execute(
            f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION grademind_forbid_mutation()"
        )

    op.execute("""
        CREATE FUNCTION grademind_submission_finalized(sid uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT coalesce((SELECT action FROM finalization_events WHERE submission_id = sid ORDER BY seq DESC LIMIT 1) = 'FINALIZED', false)
        $$;
    """)
    op.execute("""
        CREATE FUNCTION grademind_refuse_when_finalized() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE sid uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN sid := OLD.submission_id; ELSE sid := NEW.submission_id; END IF;
            PERFORM 1 FROM submissions WHERE id = sid FOR SHARE;
            IF grademind_submission_finalized(sid) THEN
                RAISE EXCEPTION 'submission % is finalized: % on % is not allowed until it is reopened (I8)', sid, TG_OP, TG_TABLE_NAME
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$;
    """)
    for table, ops in GUARDED:
        op.execute(
            f"CREATE TRIGGER {table}_finalized_guard BEFORE {ops} ON {table} FOR EACH ROW EXECUTE FUNCTION grademind_refuse_when_finalized()"
        )
    op.execute("""
        CREATE FUNCTION grademind_check_finalization_order() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE prev text;
        BEGIN
            PERFORM 1 FROM submissions WHERE id = NEW.submission_id FOR UPDATE;
            SELECT action INTO prev FROM finalization_events WHERE submission_id = NEW.submission_id ORDER BY seq DESC LIMIT 1;
            IF NEW.action = 'FINALIZED' AND prev = 'FINALIZED' THEN
                RAISE EXCEPTION 'submission % is already finalized', NEW.submission_id USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.action = 'REOPENED' AND prev IS DISTINCT FROM 'FINALIZED' THEN
                RAISE EXCEPTION 'submission % is not finalized, so it cannot be reopened', NEW.submission_id USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER finalization_events_order BEFORE INSERT ON finalization_events FOR EACH ROW EXECUTE FUNCTION grademind_check_finalization_order()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS finalization_events_order ON finalization_events")
    op.execute("DROP FUNCTION IF EXISTS grademind_check_finalization_order()")
    for table, _ in GUARDED:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_finalized_guard ON {table}")
    op.execute("DROP FUNCTION IF EXISTS grademind_refuse_when_finalized()")
    op.execute("DROP FUNCTION IF EXISTS grademind_submission_finalized(uuid)")
    for t in ("finalization_events", "result_snapshots"):
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
    op.drop_index("ix_finalization_events_submission_id", table_name="finalization_events")
    op.drop_table("finalization_events")
    op.drop_index("ix_result_snapshots_exam_id", table_name="result_snapshots")
    op.drop_index("ix_result_snapshots_submission_id", table_name="result_snapshots")
    op.drop_table("result_snapshots")
