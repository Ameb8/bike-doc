"""Create diagnostic persistence schema.

Revision ID: 0001
Revises:
Create Date: 2026-06-23 00:01:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create app-owned diagnostic persistence tables."""
    op.execute(
        """
        CREATE FUNCTION set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$;
        """,
    )

    op.execute(
        """
        CREATE TABLE users (
          id text PRIMARY KEY,
          auth_subject text NOT NULL,
          email text NOT NULL,
          display_name text NOT NULL,
          skill_level text NOT NULL DEFAULT 'unknown',
          created_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT ck_users_id_prefix CHECK (id LIKE 'usr_%'),
          CONSTRAINT ck_users_skill_level
            CHECK (skill_level IN (
              'unknown', 'beginner', 'intermediate', 'advanced'
            )),
          CONSTRAINT ck_users_email_not_blank CHECK (length(trim(email)) > 0),
          CONSTRAINT ck_users_display_name_not_blank
            CHECK (length(trim(display_name)) > 0)
        );
        """,
    )

    op.execute(
        """
        CREATE TABLE bike_profiles (
          id text PRIMARY KEY,
          user_id text NOT NULL,
          display_name text NOT NULL,
          make text NULL,
          model text NULL,
          model_year integer NULL,
          bike_type text NOT NULL DEFAULT 'unknown',
          frame_material text NOT NULL DEFAULT 'unknown',
          drivetrain text NULL,
          brake_type text NOT NULL DEFAULT 'unknown',
          wheel_size text NULL,
          tire_size text NULL,
          notes text NULL,
          deleted_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT fk_bike_profiles_user
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
          CONSTRAINT ck_bike_profiles_id_prefix CHECK (id LIKE 'bike_%'),
          CONSTRAINT ck_bike_profiles_display_name_not_blank
            CHECK (length(trim(display_name)) > 0),
          CONSTRAINT ck_bike_profiles_model_year
            CHECK (model_year IS NULL OR model_year BETWEEN 1880 AND 2100),
          CONSTRAINT ck_bike_profiles_bike_type
            CHECK (bike_type IN (
              'unknown', 'road', 'gravel', 'mountain', 'hybrid',
              'commuter', 'cargo', 'ebike', 'other'
            )),
          CONSTRAINT ck_bike_profiles_frame_material
            CHECK (frame_material IN (
              'unknown', 'aluminum', 'steel', 'carbon', 'titanium', 'other'
            )),
          CONSTRAINT ck_bike_profiles_brake_type
            CHECK (brake_type IN (
              'unknown', 'rim', 'mechanical_disc', 'hydraulic_disc',
              'coaster', 'other'
            ))
        );
        """,
    )
    op.execute(
        """
        CREATE TRIGGER trg_bike_profiles_set_updated_at
        BEFORE UPDATE ON bike_profiles
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
        """,
    )

    op.execute(
        """
        CREATE TABLE repair_sessions (
          id text PRIMARY KEY,
          user_id text NOT NULL,
          bike_id text NOT NULL,
          client_session_id text NULL,
          request_hash text NULL,
          phase text NOT NULL DEFAULT 'diagnostic',
          status text NOT NULL DEFAULT 'created',
          safety_state text NOT NULL DEFAULT 'ok',
          current_input_request jsonb NULL,
          execution_progress jsonb NULL,
          active_safety_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
          latest_event_sequence bigint NOT NULL DEFAULT 0,
          diagnostic_report_id text NULL,
          plan_report_id text NULL,
          execution_report_id text NULL,
          shop_referral_report_id text NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT fk_repair_sessions_user
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
          CONSTRAINT fk_repair_sessions_bike
            FOREIGN KEY (bike_id) REFERENCES bike_profiles (id)
            ON DELETE RESTRICT,
          CONSTRAINT ck_repair_sessions_id_prefix CHECK (id LIKE 'rs_%'),
          CONSTRAINT ck_repair_sessions_client_hash_pair
            CHECK (
              (client_session_id IS NULL AND request_hash IS NULL)
              OR (client_session_id IS NOT NULL AND request_hash IS NOT NULL)
            ),
          CONSTRAINT ck_repair_sessions_phase
            CHECK (phase IN (
              'diagnostic', 'planning', 'execution',
              'completed', 'shop_referred', 'cancelled'
            )),
          CONSTRAINT ck_repair_sessions_status
            CHECK (status IN (
              'created', 'running', 'awaiting_user', 'awaiting_decision',
              'blocked_safety', 'completed', 'failed', 'cancelled'
            )),
          CONSTRAINT ck_repair_sessions_safety_state
            CHECK (safety_state IN (
              'ok', 'caution', 'shop_recommended', 'blocked'
            )),
          CONSTRAINT ck_repair_sessions_current_input_request_object
            CHECK (
              current_input_request IS NULL
              OR jsonb_typeof(current_input_request) = 'object'
            ),
          CONSTRAINT ck_repair_sessions_execution_progress_object
            CHECK (
              execution_progress IS NULL
              OR jsonb_typeof(execution_progress) = 'object'
            ),
          CONSTRAINT ck_repair_sessions_active_safety_flags_array
            CHECK (jsonb_typeof(active_safety_flags) = 'array'),
          CONSTRAINT ck_repair_sessions_latest_event_sequence
            CHECK (latest_event_sequence >= 0)
        );
        """,
    )
    op.execute(
        """
        CREATE TRIGGER trg_repair_sessions_set_updated_at
        BEFORE UPDATE ON repair_sessions
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
        """,
    )

    op.execute(
        """
        CREATE TABLE repair_phase_sessions (
          id text PRIMARY KEY,
          repair_session_id text NOT NULL,
          phase text NOT NULL,
          adk_session_id text NOT NULL,
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          closed_at timestamptz NULL,

          CONSTRAINT fk_repair_phase_sessions_repair_session
            FOREIGN KEY (repair_session_id)
            REFERENCES repair_sessions (id)
            ON DELETE CASCADE,
          CONSTRAINT ck_repair_phase_sessions_id_prefix
            CHECK (id LIKE 'phs_%'),
          CONSTRAINT ck_repair_phase_sessions_phase
            CHECK (phase IN ('diagnostic', 'planning', 'execution')),
          CONSTRAINT ck_repair_phase_sessions_status
            CHECK (status IN ('active', 'closed')),
          CONSTRAINT ck_repair_phase_sessions_closed_at
            CHECK (
              (status = 'active' AND closed_at IS NULL)
              OR (status = 'closed' AND closed_at IS NOT NULL)
            ),
          CONSTRAINT ck_repair_phase_sessions_adk_session_id_not_blank
            CHECK (length(trim(adk_session_id)) > 0)
        );
        """,
    )

    op.execute(
        """
        CREATE TABLE repair_turns (
          id text PRIMARY KEY,
          repair_session_id text NOT NULL,
          repair_phase_session_id text NOT NULL,
          client_turn_id text NOT NULL,
          request_hash text NOT NULL,
          schema_version text NOT NULL DEFAULT 'ai_turn.v1',
          phase text NOT NULL,
          message jsonb NOT NULL,
          responds_to_input_request_id text NULL,
          start_event_sequence bigint NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT fk_repair_turns_repair_session
            FOREIGN KEY (repair_session_id)
            REFERENCES repair_sessions (id)
            ON DELETE CASCADE,
          CONSTRAINT fk_repair_turns_repair_phase_session
            FOREIGN KEY (repair_phase_session_id)
            REFERENCES repair_phase_sessions (id)
            ON DELETE CASCADE,
          CONSTRAINT ck_repair_turns_id_prefix CHECK (id LIKE 'turn_%'),
          CONSTRAINT ck_repair_turns_client_turn_id_not_blank
            CHECK (length(trim(client_turn_id)) > 0),
          CONSTRAINT ck_repair_turns_schema_version
            CHECK (schema_version = 'ai_turn.v1'),
          CONSTRAINT ck_repair_turns_phase
            CHECK (phase IN ('diagnostic', 'planning', 'execution')),
          CONSTRAINT ck_repair_turns_message_object
            CHECK (jsonb_typeof(message) = 'object'),
          CONSTRAINT ck_repair_turns_start_event_sequence
            CHECK (start_event_sequence >= 1)
        );
        """,
    )

    op.execute(
        """
        CREATE TABLE repair_session_events (
          id text PRIMARY KEY,
          repair_session_id text NOT NULL,
          turn_id text NULL,
          sequence bigint NOT NULL,
          type text NOT NULL,
          data jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT fk_repair_session_events_repair_session
            FOREIGN KEY (repair_session_id)
            REFERENCES repair_sessions (id)
            ON DELETE CASCADE,
          CONSTRAINT fk_repair_session_events_turn
            FOREIGN KEY (turn_id)
            REFERENCES repair_turns (id)
            ON DELETE SET NULL,
          CONSTRAINT ck_repair_session_events_id_prefix
            CHECK (id LIKE 'evt_%'),
          CONSTRAINT ck_repair_session_events_sequence CHECK (sequence >= 1),
          CONSTRAINT ck_repair_session_events_type
            CHECK (type IN (
              'turn.started',
              'assistant.delta',
              'assistant.message.completed',
              'input.requested',
              'artifact.referenced',
              'phase.report.created',
              'phase.transitioned',
              'safety.escalated',
              'execution.step.updated',
              'turn.completed',
              'error',
              'heartbeat'
            )),
          CONSTRAINT ck_repair_session_events_data_object
            CHECK (jsonb_typeof(data) = 'object')
        );
        """,
    )

    op.execute(
        """
        CREATE TABLE artifact_refs (
          id text PRIMARY KEY,
          user_id text NOT NULL,
          repair_session_id text NULL,
          bike_id text NULL,
          client_artifact_id text NULL,
          request_hash text NULL,
          purpose text NOT NULL,
          media_type text NOT NULL,
          mime_type text NOT NULL,
          filename text NOT NULL,
          byte_size bigint NOT NULL,
          width integer NULL,
          height integer NULL,
          duration_seconds numeric(10, 3) NULL,
          status text NOT NULL DEFAULT 'uploaded',
          rejection_reason text NULL,
          content_sha256 text NOT NULL,
          storage_provider text NOT NULL,
          storage_bucket text NULL,
          storage_path text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT fk_artifact_refs_user
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
          CONSTRAINT fk_artifact_refs_repair_session
            FOREIGN KEY (repair_session_id)
            REFERENCES repair_sessions (id)
            ON DELETE SET NULL,
          CONSTRAINT fk_artifact_refs_bike
            FOREIGN KEY (bike_id)
            REFERENCES bike_profiles (id)
            ON DELETE SET NULL,
          CONSTRAINT ck_artifact_refs_id_prefix CHECK (id LIKE 'art_%'),
          CONSTRAINT ck_artifact_refs_client_hash_pair
            CHECK (
              (client_artifact_id IS NULL AND request_hash IS NULL)
              OR (client_artifact_id IS NOT NULL AND request_hash IS NOT NULL)
            ),
          CONSTRAINT ck_artifact_refs_purpose
            CHECK (purpose IN (
              'diagnostic_photo',
              'verification_photo',
              'bike_profile_photo',
              'repair_reference',
              'other'
            )),
          CONSTRAINT ck_artifact_refs_parent_by_purpose
            CHECK (
              (
                purpose IN ('diagnostic_photo', 'verification_photo')
                AND repair_session_id IS NOT NULL
                AND bike_id IS NULL
              )
              OR (
                purpose = 'bike_profile_photo'
                AND bike_id IS NOT NULL
                AND repair_session_id IS NULL
              )
              OR (
                purpose IN ('repair_reference', 'other')
              )
            ),
          CONSTRAINT ck_artifact_refs_media_type
            CHECK (media_type IN ('image', 'video', 'audio', 'document', 'other')),
          CONSTRAINT ck_artifact_refs_byte_size CHECK (byte_size >= 0),
          CONSTRAINT ck_artifact_refs_dimensions
            CHECK (
              (width IS NULL OR width > 0)
              AND (height IS NULL OR height > 0)
            ),
          CONSTRAINT ck_artifact_refs_duration
            CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
          CONSTRAINT ck_artifact_refs_status
            CHECK (status IN ('uploaded', 'processing', 'ready', 'rejected')),
          CONSTRAINT ck_artifact_refs_rejection_reason
            CHECK (
              (status = 'rejected' AND rejection_reason IS NOT NULL)
              OR (status <> 'rejected')
            ),
          CONSTRAINT ck_artifact_refs_filename_not_blank
            CHECK (length(trim(filename)) > 0),
          CONSTRAINT ck_artifact_refs_content_sha256
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_artifact_refs_storage_provider_not_blank
            CHECK (length(trim(storage_provider)) > 0),
          CONSTRAINT ck_artifact_refs_storage_path_not_blank
            CHECK (length(trim(storage_path)) > 0)
        );
        """,
    )
    op.execute(
        """
        CREATE TRIGGER trg_artifact_refs_set_updated_at
        BEFORE UPDATE ON artifact_refs
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
        """,
    )

    op.execute(
        """
        CREATE TABLE phase_reports (
          id text PRIMARY KEY,
          repair_session_id text NOT NULL,
          repair_phase_session_id text NULL,
          type text NOT NULL,
          schema_version text NOT NULL,
          phase text NOT NULL,
          summary text NOT NULL,
          safety_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
          source_artifact_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
          payload jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT fk_phase_reports_repair_session
            FOREIGN KEY (repair_session_id)
            REFERENCES repair_sessions (id)
            ON DELETE CASCADE,
          CONSTRAINT fk_phase_reports_repair_phase_session
            FOREIGN KEY (repair_phase_session_id)
            REFERENCES repair_phase_sessions (id)
            ON DELETE SET NULL,
          CONSTRAINT ck_phase_reports_id_prefix CHECK (id LIKE 'rpt_%'),
          CONSTRAINT ck_phase_reports_type
            CHECK (type IN (
              'diagnostic', 'plan', 'execution', 'shop_referral'
            )),
          CONSTRAINT ck_phase_reports_phase
            CHECK (phase IN (
              'diagnostic', 'planning', 'execution',
              'completed', 'shop_referred', 'cancelled'
            )),
          CONSTRAINT ck_phase_reports_schema_version
            CHECK (schema_version IN (
              'diagnostic_report.v1',
              'plan_report.v1',
              'execution_report.v1',
              'shop_referral_report.v1'
            )),
          CONSTRAINT ck_phase_reports_type_schema_version_pair
            CHECK (
              (type = 'diagnostic'
                AND schema_version = 'diagnostic_report.v1')
              OR (type = 'plan' AND schema_version = 'plan_report.v1')
              OR (type = 'execution'
                AND schema_version = 'execution_report.v1')
              OR (type = 'shop_referral'
                AND schema_version = 'shop_referral_report.v1')
            ),
          CONSTRAINT ck_phase_reports_summary_not_blank
            CHECK (length(trim(summary)) > 0),
          CONSTRAINT ck_phase_reports_safety_flags_array
            CHECK (jsonb_typeof(safety_flags) = 'array'),
          CONSTRAINT ck_phase_reports_source_artifact_ids_array
            CHECK (jsonb_typeof(source_artifact_ids) = 'array'),
          CONSTRAINT ck_phase_reports_payload_object
            CHECK (jsonb_typeof(payload) = 'object')
        );
        """,
    )

    op.execute(
        """
        ALTER TABLE repair_sessions
          ADD CONSTRAINT fk_repair_sessions_diagnostic_report
            FOREIGN KEY (diagnostic_report_id)
            REFERENCES phase_reports (id)
            ON DELETE SET NULL
            DEFERRABLE INITIALLY DEFERRED,
          ADD CONSTRAINT fk_repair_sessions_plan_report
            FOREIGN KEY (plan_report_id)
            REFERENCES phase_reports (id)
            ON DELETE SET NULL
            DEFERRABLE INITIALLY DEFERRED,
          ADD CONSTRAINT fk_repair_sessions_execution_report
            FOREIGN KEY (execution_report_id)
            REFERENCES phase_reports (id)
            ON DELETE SET NULL
            DEFERRABLE INITIALLY DEFERRED,
          ADD CONSTRAINT fk_repair_sessions_shop_referral_report
            FOREIGN KEY (shop_referral_report_id)
            REFERENCES phase_reports (id)
            ON DELETE SET NULL
            DEFERRABLE INITIALLY DEFERRED;
        """,
    )

    op.execute("CREATE UNIQUE INDEX ux_users_auth_subject ON users (auth_subject);")
    op.execute("CREATE INDEX ix_users_email ON users (email);")
    op.execute(
        """
        CREATE INDEX ix_bike_profiles_user_created
          ON bike_profiles (user_id, created_at DESC, id DESC)
          WHERE deleted_at IS NULL;
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_repair_sessions_user_client_session
          ON repair_sessions (user_id, client_session_id)
          WHERE client_session_id IS NOT NULL;
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_repair_sessions_user_created
          ON repair_sessions (user_id, created_at DESC, id DESC);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_repair_sessions_user_status_created
          ON repair_sessions (user_id, status, created_at DESC, id DESC);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_repair_sessions_bike_created
          ON repair_sessions (bike_id, created_at DESC, id DESC);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_repair_sessions_active_safety_flags_gin
          ON repair_sessions USING gin (active_safety_flags);
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_repair_phase_sessions_session_phase
          ON repair_phase_sessions (repair_session_id, phase);
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_repair_phase_sessions_adk_session
          ON repair_phase_sessions (adk_session_id);
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_repair_turns_session_client_turn
          ON repair_turns (repair_session_id, client_turn_id);
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_repair_turns_session_start_event
          ON repair_turns (repair_session_id, start_event_sequence);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_repair_turns_session_created
          ON repair_turns (repair_session_id, created_at ASC, id ASC);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_repair_turns_phase_session
          ON repair_turns (repair_phase_session_id);
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_repair_session_events_session_sequence
          ON repair_session_events (repair_session_id, sequence);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_repair_session_events_turn_sequence
          ON repair_session_events (turn_id, sequence)
          WHERE turn_id IS NOT NULL;
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_artifact_refs_user_client_artifact
          ON artifact_refs (user_id, client_artifact_id)
          WHERE client_artifact_id IS NOT NULL;
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_artifact_refs_session_created
          ON artifact_refs (repair_session_id, created_at DESC, id DESC)
          WHERE repair_session_id IS NOT NULL;
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_artifact_refs_bike_created
          ON artifact_refs (bike_id, created_at DESC, id DESC)
          WHERE bike_id IS NOT NULL;
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_artifact_refs_user_created
          ON artifact_refs (user_id, created_at DESC, id DESC);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_phase_reports_session_created
          ON phase_reports (repair_session_id, created_at DESC, id DESC);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_phase_reports_session_type_created
          ON phase_reports (repair_session_id, type, created_at DESC, id DESC);
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_phase_reports_phase_session_created
          ON phase_reports (repair_phase_session_id, created_at DESC, id DESC)
          WHERE repair_phase_session_id IS NOT NULL;
        """,
    )


def downgrade() -> None:
    """Drop app-owned diagnostic persistence tables."""
    op.execute("DROP TABLE phase_reports;")
    op.execute("DROP TABLE artifact_refs;")
    op.execute("DROP TABLE repair_session_events;")
    op.execute("DROP TABLE repair_turns;")
    op.execute("DROP TABLE repair_phase_sessions;")
    op.execute("DROP TABLE repair_sessions;")
    op.execute("DROP TABLE bike_profiles;")
    op.execute("DROP TABLE users;")
    op.execute("DROP FUNCTION set_updated_at();")
