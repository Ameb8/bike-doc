"""Add versioned shadow profile-inference runs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-11 01:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create durable, idempotent records for isolated image extraction."""

    op.execute(
        """
        CREATE TABLE profile_inference_runs (
          id text PRIMARY KEY,
          turn_id text NOT NULL REFERENCES repair_turns(id) ON DELETE RESTRICT,
          repair_session_id text NOT NULL
            REFERENCES repair_sessions(id) ON DELETE RESTRICT,
          bike_id text NOT NULL REFERENCES bike_profiles(id) ON DELETE RESTRICT,
          inference_schema_version text NOT NULL,
          extractor_version text NOT NULL,
          input_artifact_ids jsonb NOT NULL,
          status text NOT NULL DEFAULT 'running',
          claim_count integer NOT NULL DEFAULT 0,
          failure_code text NULL,
          attempt_count integer NOT NULL DEFAULT 1,
          started_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT ck_profile_inference_runs_id_prefix CHECK (id LIKE 'pir_%'),
          CONSTRAINT ck_profile_inference_runs_status CHECK (status IN (
            'running', 'completed', 'abstained', 'retryable', 'failed'
          )),
          CONSTRAINT ck_profile_inference_runs_claim_count CHECK (claim_count >= 0),
          CONSTRAINT ck_profile_inference_runs_attempt_count CHECK (attempt_count >= 1),
          CONSTRAINT ux_profile_inference_runs_turn_schema_extractor UNIQUE (
            turn_id, inference_schema_version, extractor_version
          )
        );
        """,
    )


def downgrade() -> None:
    """Remove profile-inference run persistence."""

    op.execute("DROP TABLE profile_inference_runs;")
