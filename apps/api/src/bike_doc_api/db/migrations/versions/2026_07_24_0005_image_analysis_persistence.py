"""Persist image-analysis mode snapshots and extraction-run records.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-24 12:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add immutable turn mode snapshots and durable extraction execution history."""

    op.execute(
        "ALTER TABLE repair_turns ADD COLUMN image_analysis_mode text NULL;",
    )
    op.execute(
        "ALTER TABLE repair_turns ADD CONSTRAINT ux_repair_turns_id_session "
        "UNIQUE (id, repair_session_id);",
    )
    op.execute(
        """
        CREATE FUNCTION jsonb_array_has_unique_text_values(value jsonb)
        RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
          SELECT jsonb_typeof(value) = 'array'
            AND NOT EXISTS (
              SELECT 1 FROM jsonb_array_elements_text(value) AS item(value)
              GROUP BY item.value HAVING count(*) > 1
            );
        $$;
        """,
    )
    op.execute(
        """
        ALTER TABLE repair_turns
        ADD CONSTRAINT ck_repair_turns_image_analysis_mode
        CHECK (image_analysis_mode IS NULL OR image_analysis_mode IN (
          'off', 'pixels_only', 'shadow', 'enabled'
        ));
        """,
    )
    op.execute(
        """
        CREATE FUNCTION prevent_repair_turn_image_analysis_mode_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.image_analysis_mode IS DISTINCT FROM NEW.image_analysis_mode THEN
            RAISE EXCEPTION 'repair turn image analysis mode is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        """,
    )
    op.execute(
        """
        CREATE TRIGGER trg_repair_turns_image_analysis_mode_immutable
        BEFORE UPDATE ON repair_turns
        FOR EACH ROW EXECUTE FUNCTION prevent_repair_turn_image_analysis_mode_change();
        """,
    )
    op.execute(
        """
        CREATE TABLE observation_extraction_runs (
          id text PRIMARY KEY,
          turn_id text NOT NULL,
          repair_session_id text NOT NULL,
          image_analysis_mode text NOT NULL,
          input_artifact_ids jsonb NOT NULL,
          preprocessing_version text NOT NULL,
          extractor_version text NOT NULL,
          prompt_version text NOT NULL,
          output_schema_version text NOT NULL,
          provider text NOT NULL,
          model text NOT NULL,
          preprocessing_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
          status text NOT NULL DEFAULT 'pending',
          validated_output jsonb NULL,
          failure_metadata jsonb NULL,
          provider_attempt_count integer NOT NULL DEFAULT 0,
          provider_latency_ms bigint NOT NULL DEFAULT 0,
          input_tokens bigint NULL,
          output_tokens bigint NULL,
          provider_cost_microunits bigint NULL,
          diagnostic_agent_started_at timestamptz NULL,
          redacted_at timestamptz NULL,
          redaction_reason text NULL,
          completed_at timestamptz NULL,
          failed_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT ck_observation_extraction_runs_id_prefix
            CHECK (id LIKE 'oer_%'),
          CONSTRAINT ux_observation_extraction_runs_turn UNIQUE (turn_id),
          CONSTRAINT ck_observation_extraction_runs_mode
            CHECK (image_analysis_mode IN ('shadow', 'enabled')),
          CONSTRAINT ck_observation_extraction_runs_input_artifact_ids_array
            CHECK (jsonb_typeof(input_artifact_ids) = 'array'),
          CONSTRAINT ck_observation_extraction_runs_input_artifact_ids_unique
            CHECK (jsonb_array_has_unique_text_values(input_artifact_ids)),
          CONSTRAINT ck_observation_extraction_runs_preprocessing_manifest_array
            CHECK (jsonb_typeof(preprocessing_manifest) = 'array'),
          CONSTRAINT ck_observation_extraction_runs_status
            CHECK (status IN ('pending', 'completed', 'failed')),
          CONSTRAINT ck_observation_extraction_runs_validated_output_object
            CHECK (validated_output IS NULL
              OR jsonb_typeof(validated_output) = 'object'),
          CONSTRAINT ck_observation_extraction_runs_failure_metadata_object
            CHECK (failure_metadata IS NULL
              OR jsonb_typeof(failure_metadata) = 'object'),
          CONSTRAINT ck_observation_extraction_runs_failure_metadata_size
            CHECK (failure_metadata IS NULL
              OR octet_length(failure_metadata::text) <= 4096),
          CONSTRAINT ck_observation_extraction_runs_attempt_count
            CHECK (provider_attempt_count >= 0),
          CONSTRAINT ck_observation_extraction_runs_latency
            CHECK (provider_latency_ms >= 0),
          CONSTRAINT ck_observation_extraction_runs_input_tokens
            CHECK (input_tokens IS NULL OR input_tokens >= 0),
          CONSTRAINT ck_observation_extraction_runs_output_tokens
            CHECK (output_tokens IS NULL OR output_tokens >= 0),
          CONSTRAINT ck_observation_extraction_runs_cost
            CHECK (provider_cost_microunits IS NULL OR provider_cost_microunits >= 0),
          CONSTRAINT ck_observation_extraction_runs_terminal_timestamps
            CHECK (
              (status = 'pending' AND completed_at IS NULL AND failed_at IS NULL)
              OR (status = 'completed' AND completed_at IS NOT NULL
                  AND failed_at IS NULL)
              OR (status = 'failed' AND failed_at IS NOT NULL AND completed_at IS NULL)
            ),
          CONSTRAINT ck_observation_extraction_runs_redaction_pair
            CHECK (
              (redacted_at IS NULL AND redaction_reason IS NULL)
              OR (redacted_at IS NOT NULL AND redaction_reason IS NOT NULL)
            ),
          CONSTRAINT fk_observation_extraction_runs_turn_session
            FOREIGN KEY (turn_id, repair_session_id)
            REFERENCES repair_turns (id, repair_session_id)
            ON DELETE CASCADE
        );
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_observation_extraction_runs_session_usable
        ON observation_extraction_runs (repair_session_id, created_at ASC, id ASC)
        WHERE status = 'completed' AND redacted_at IS NULL;
        """,
    )
    op.execute(
        """
        CREATE TRIGGER trg_observation_extraction_runs_set_updated_at
        BEFORE UPDATE ON observation_extraction_runs
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """,
    )
    op.execute(
        """
        CREATE FUNCTION enforce_observation_extraction_run_write_once_fields()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.diagnostic_agent_started_at IS NOT NULL
             AND OLD.diagnostic_agent_started_at IS DISTINCT FROM
                 NEW.diagnostic_agent_started_at THEN
            RAISE EXCEPTION 'diagnostic agent start marker is write-once';
          END IF;
          IF OLD.diagnostic_agent_started_at IS NOT NULL
             AND OLD.status IS DISTINCT FROM NEW.status THEN
            RAISE EXCEPTION 'extraction closed after agent start';
          END IF;
          IF OLD.redacted_at IS NOT NULL
             AND (OLD.redacted_at IS DISTINCT FROM NEW.redacted_at
                  OR OLD.redaction_reason IS DISTINCT FROM NEW.redaction_reason) THEN
            RAISE EXCEPTION 'observation extraction redaction is irreversible';
          END IF;
          RETURN NEW;
        END;
        $$;
        """,
    )
    op.execute(
        """
        CREATE TRIGGER trg_observation_extraction_runs_write_once
        BEFORE UPDATE ON observation_extraction_runs
        FOR EACH ROW EXECUTE FUNCTION
          enforce_observation_extraction_run_write_once_fields();
        """,
    )
    op.execute(
        """
        CREATE TABLE observation_extraction_attempts (
          id text PRIMARY KEY,
          run_id text NOT NULL
            REFERENCES observation_extraction_runs(id) ON DELETE CASCADE,
          attempt_number integer NOT NULL,
          provider text NOT NULL,
          model text NOT NULL,
          outcome text NOT NULL,
          failure_metadata jsonb NULL,
          started_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz NULL,
          latency_ms bigint NULL,
          input_tokens bigint NULL,
          output_tokens bigint NULL,
          provider_cost_microunits bigint NULL,
          created_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT ck_observation_extraction_attempts_id_prefix
            CHECK (id LIKE 'oea_%'),
          CONSTRAINT ux_observation_extraction_attempts_run_number
            UNIQUE (run_id, attempt_number),
          CONSTRAINT ck_observation_extraction_attempts_number
            CHECK (attempt_number >= 1),
          CONSTRAINT ck_observation_extraction_attempts_outcome
            CHECK (outcome IN ('pending', 'completed', 'failed')),
          CONSTRAINT ck_observation_extraction_attempts_failure_metadata_object
            CHECK (failure_metadata IS NULL
              OR jsonb_typeof(failure_metadata) = 'object'),
          CONSTRAINT ck_observation_extraction_attempts_failure_metadata_size
            CHECK (failure_metadata IS NULL
              OR octet_length(failure_metadata::text) <= 4096),
          CONSTRAINT ck_observation_extraction_attempts_latency
            CHECK (latency_ms IS NULL OR latency_ms >= 0),
          CONSTRAINT ck_observation_extraction_attempts_input_tokens
            CHECK (input_tokens IS NULL OR input_tokens >= 0),
          CONSTRAINT ck_observation_extraction_attempts_output_tokens
            CHECK (output_tokens IS NULL OR output_tokens >= 0),
          CONSTRAINT ck_observation_extraction_attempts_cost
            CHECK (provider_cost_microunits IS NULL OR provider_cost_microunits >= 0),
          CONSTRAINT ck_observation_extraction_attempts_completed_at
            CHECK (
              (outcome = 'pending' AND completed_at IS NULL)
              OR (outcome IN ('completed', 'failed') AND completed_at IS NOT NULL)
            )
        );
        """,
    )
    op.execute(
        "CREATE INDEX ix_observation_extraction_attempts_run_order "
        "ON observation_extraction_attempts (run_id, attempt_number);",
    )


def downgrade() -> None:
    """Remove image-analysis persistence."""

    op.execute("DROP TABLE observation_extraction_attempts;")
    op.execute("DROP TABLE observation_extraction_runs;")
    op.execute("DROP FUNCTION enforce_observation_extraction_run_write_once_fields();")
    op.execute(
        "DROP TRIGGER trg_repair_turns_image_analysis_mode_immutable ON repair_turns;",
    )
    op.execute("DROP FUNCTION prevent_repair_turn_image_analysis_mode_change();")
    op.execute("DROP FUNCTION jsonb_array_has_unique_text_values(jsonb);")
    op.execute(
        "ALTER TABLE repair_turns DROP CONSTRAINT ux_repair_turns_id_session;",
    )
    op.execute(
        "ALTER TABLE repair_turns DROP CONSTRAINT ck_repair_turns_image_analysis_mode;",
    )
    op.execute("ALTER TABLE repair_turns DROP COLUMN image_analysis_mode;")
