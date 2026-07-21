"""Harden profile-inference run lifecycle and retry classification."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade existing run rows to the stable lifecycle vocabulary."""

    op.execute(
        "ALTER TABLE profile_inference_runs "
        "DROP CONSTRAINT ck_profile_inference_runs_status;",
    )
    op.execute("ALTER TABLE profile_inference_runs ADD COLUMN failure_class text NULL;")
    op.execute(
        "ALTER TABLE profile_inference_runs "
        "ADD COLUMN retry_count integer NOT NULL DEFAULT 0;",
    )
    op.execute(
        "ALTER TABLE profile_inference_runs "
        "ADD COLUMN max_attempts integer NOT NULL DEFAULT 1;",
    )
    op.execute(
        "ALTER TABLE profile_inference_runs "
        "ADD COLUMN lifecycle_outcomes jsonb NOT NULL DEFAULT '[\"started\"]'::jsonb;",
    )
    op.execute(
        "UPDATE profile_inference_runs SET status = 'started' WHERE status = 'running';"
    )
    op.execute(
        "UPDATE profile_inference_runs "
        "SET status = 'retryable_failure' WHERE status = 'retryable';",
    )
    op.execute(
        "UPDATE profile_inference_runs "
        "SET status = 'terminal_failure' WHERE status = 'failed';",
    )
    op.execute(
        """
        ALTER TABLE profile_inference_runs
        ADD CONSTRAINT ck_profile_inference_runs_status
        CHECK (status IN (
          'started', 'completed', 'abstained', 'retryable_failure',
          'terminal_failure', 'exhausted'
        ));
        """,
    )
    op.execute(
        "ALTER TABLE profile_inference_runs "
        "ADD CONSTRAINT ck_profile_inference_runs_retry_count "
        "CHECK (retry_count >= 0);",
    )
    op.execute(
        "ALTER TABLE profile_inference_runs "
        "ADD CONSTRAINT ck_profile_inference_runs_max_attempts "
        "CHECK (max_attempts >= 1);",
    )


def downgrade() -> None:
    """Restore the prior run status vocabulary."""

    op.execute(
        "UPDATE profile_inference_runs SET status = 'running' WHERE status = 'started';"
    )
    op.execute(
        "UPDATE profile_inference_runs SET status = 'retryable' "
        "WHERE status IN ('retryable_failure', 'exhausted');",
    )
    op.execute(
        "UPDATE profile_inference_runs "
        "SET status = 'failed' WHERE status = 'terminal_failure';",
    )
    op.execute(
        "ALTER TABLE profile_inference_runs "
        "DROP CONSTRAINT ck_profile_inference_runs_status;",
    )
    op.execute(
        """
        ALTER TABLE profile_inference_runs
        ADD CONSTRAINT ck_profile_inference_runs_status
        CHECK (status IN ('running', 'completed', 'abstained', 'retryable', 'failed'));
        """,
    )
    op.execute("ALTER TABLE profile_inference_runs DROP COLUMN lifecycle_outcomes;")
    op.execute("ALTER TABLE profile_inference_runs DROP COLUMN max_attempts;")
    op.execute("ALTER TABLE profile_inference_runs DROP COLUMN retry_count;")
    op.execute("ALTER TABLE profile_inference_runs DROP COLUMN failure_class;")
