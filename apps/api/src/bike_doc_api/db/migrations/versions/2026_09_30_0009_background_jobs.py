"""Create durable background work and lifecycle constraints.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install jobs and guard immutable definitions/monotonic lifecycle data."""
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.String(30), nullable=False, primary_key=True),
        sa.Column("job_kind", sa.String(64), nullable=False),
        sa.Column("workload_class", sa.String(64), nullable=False),
        sa.Column("input_version", sa.Integer(), nullable=False),
        sa.Column("input", JSONB(), nullable=False),
        sa.Column("deduplication_key", sa.String(256), nullable=False),
        sa.Column(
            "state", sa.String(16), nullable=False, server_default=sa.text("'queued'")
        ),
        sa.Column(
            "eligible_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("attempt_limit", sa.Integer(), nullable=False),
        sa.Column(
            "desired_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "confirmed_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "publication_eligible_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("publication_claim_token", sa.String(32), nullable=True),
        sa.Column("publication_claim_generation", sa.BigInteger(), nullable=True),
        sa.Column("publication_claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "publication_confirmed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("transport_error", sa.String(32), nullable=True),
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_token", sa.String(32), nullable=True),
        sa.Column("effect_boundary_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_error_category", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= attempt_limit AND "
            "attempt_limit BETWEEN 1 AND 100",
            name="ck_background_jobs_attempts",
        ),
        sa.CheckConstraint(
            "(confirmed_generation = 0) = (publication_confirmed_at IS NULL)",
            name="ck_background_jobs_confirmation",
        ),
        sa.CheckConstraint(
            "execution_token IS NULL OR (attempt_count > 0 AND "
            "first_started_at IS NOT NULL AND execution_deadline > "
            "execution_started_at AND execution_started_at >= "
            "first_started_at)",
            name="ck_background_jobs_deadline",
        ),
        sa.CheckConstraint(
            "job_kind ~ '^[a-z][a-z0-9_]{0,63}$' AND workload_class ~ "
            "'^[a-z][a-z0-9_]{0,63}$' AND input_version BETWEEN 1 AND 32767 "
            "AND length(deduplication_key) BETWEEN 1 AND 256",
            name="ck_background_jobs_definition",
        ),
        sa.CheckConstraint(
            "latest_error_category IS NULL OR latest_error_category IN "
            "('definition_unknown','version_unsupported','input_invalid','attempts_exhausted','provider_unavailable','execution_timeout','execution_lost','permanent_failure')",
            name="ck_background_jobs_error",
        ),
        sa.CheckConstraint(
            "(state = 'running') = (execution_token IS NOT NULL) AND "
            "(execution_token IS NULL) = (execution_started_at IS NULL) AND "
            "(execution_token IS NULL) = (execution_deadline IS NULL)",
            name="ck_background_jobs_execution",
        ),
        sa.CheckConstraint(
            "(attempt_count = 0) = (first_started_at IS NULL)",
            name="ck_background_jobs_first_start",
        ),
        sa.CheckConstraint(
            "desired_generation >= 1 AND confirmed_generation >= 0 AND "
            "confirmed_generation <= desired_generation",
            name="ck_background_jobs_generations",
        ),
        sa.CheckConstraint(
            "id ~ '^job_[0-7][0-9A-HJKMNP-TV-Z]{25}$'", name="ck_background_jobs_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input) = 'object' AND octet_length(input::text) <= 4096",
            name="ck_background_jobs_input",
        ),
        sa.CheckConstraint(
            "(publication_claim_token IS NULL AND publication_claim_generation "
            "IS NULL AND publication_claim_until IS NULL) OR "
            "(publication_claim_token IS NOT NULL AND "
            "publication_claim_generation IS NOT NULL AND "
            "publication_claim_generation BETWEEN 1 AND desired_generation AND "
            "publication_claim_until IS NOT NULL)",
            name="ck_background_jobs_publication_claim",
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','retrying','succeeded','interrupted','dead')",
            name="ck_background_jobs_state",
        ),
        sa.CheckConstraint(
            "(state IN ('succeeded','interrupted','dead')) = (terminal_at IS NOT NULL)",
            name="ck_background_jobs_terminal",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND eligible_at >= created_at AND "
            "publication_eligible_at >= created_at AND (first_started_at IS "
            "NULL OR first_started_at >= created_at) AND (terminal_at IS NULL "
            "OR terminal_at >= COALESCE(first_started_at, created_at)) AND "
            "(effect_boundary_at IS NULL OR (first_started_at IS NOT NULL AND "
            "effect_boundary_at >= first_started_at))",
            name="ck_background_jobs_times",
        ),
        sa.CheckConstraint(
            "transport_error IS NULL OR transport_error IN "
            "('unavailable','timeout','rejected')",
            name="ck_background_jobs_transport_error",
        ),
        sa.UniqueConstraint(
            "job_kind", "deduplication_key", name="uq_background_jobs_identity"
        ),
    )
    op.create_index(
        "ix_background_jobs_deadline",
        "background_jobs",
        ["execution_deadline", "id"],
        postgresql_where=sa.text("state = 'running'"),
    )
    op.create_index(
        "ix_background_jobs_eligible",
        "background_jobs",
        ["eligible_at", "id"],
        postgresql_where=sa.text("state IN ('queued','retrying')"),
    )
    op.create_index(
        "ix_background_jobs_publication_due",
        "background_jobs",
        ["publication_eligible_at", "id"],
        postgresql_where=sa.text("desired_generation > confirmed_generation"),
    )
    op.create_index(
        "ix_background_jobs_retention",
        "background_jobs",
        ["terminal_at", "id"],
        postgresql_where=sa.text(
            "terminal_at IS NOT NULL AND desired_generation = confirmed_generation"
        ),
    )
    op.execute("""
        CREATE FUNCTION protect_background_job() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF (NEW.id, NEW.job_kind, NEW.workload_class, NEW.input_version,
              NEW.input, NEW.deduplication_key, NEW.attempt_limit, NEW.created_at)
             IS DISTINCT FROM
             (OLD.id, OLD.job_kind, OLD.workload_class, OLD.input_version,
              OLD.input, OLD.deduplication_key, OLD.attempt_limit, OLD.created_at)
          THEN
            RAISE EXCEPTION 'immutable job definition' USING ERRCODE = '23514';
          END IF;
          IF NEW.desired_generation < OLD.desired_generation
             OR NEW.confirmed_generation < OLD.confirmed_generation
             OR NEW.attempt_count < OLD.attempt_count
          THEN
            RAISE EXCEPTION 'job counters cannot decrease' USING ERRCODE = '23514';
          END IF;
          IF OLD.state IN ('succeeded', 'interrupted', 'dead') AND
             (NEW.state, NEW.terminal_at, NEW.attempt_count, NEW.eligible_at,
              NEW.execution_token, NEW.latest_error_category, NEW.effect_boundary_at,
              NEW.desired_generation)
             IS DISTINCT FROM
             (OLD.state, OLD.terminal_at, OLD.attempt_count, OLD.eligible_at,
              OLD.execution_token, OLD.latest_error_category, OLD.effect_boundary_at,
              OLD.desired_generation)
          THEN
            RAISE EXCEPTION 'terminal job outcome is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.first_started_at IS NOT NULL AND
             NEW.first_started_at IS DISTINCT FROM OLD.first_started_at
          THEN
            RAISE EXCEPTION 'first start is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.effect_boundary_at IS NOT NULL AND
             NEW.effect_boundary_at IS DISTINCT FROM OLD.effect_boundary_at
          THEN
            RAISE EXCEPTION 'effect boundary is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.execution_token IS NOT NULL
             AND NEW.execution_token = OLD.execution_token
             AND (NEW.execution_started_at, NEW.execution_deadline)
                 IS DISTINCT FROM (OLD.execution_started_at, OLD.execution_deadline)
          THEN
            RAISE EXCEPTION 'execution deadline cannot be renewed'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute(
        "CREATE TRIGGER trg_protect_background_job BEFORE UPDATE ON background_jobs "
        "FOR EACH ROW EXECUTE FUNCTION protect_background_job()"
    )


def downgrade() -> None:
    """Remove only the generic jobs table and its guard."""
    op.drop_table("background_jobs")
    op.execute("DROP FUNCTION protect_background_job()")
