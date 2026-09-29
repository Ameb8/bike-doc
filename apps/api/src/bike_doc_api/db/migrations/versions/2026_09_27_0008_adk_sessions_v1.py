"""Install the PostgreSQL ADK 2.3.0 JSON session schema (version 1).

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the complete pinned ADK storage schema without runtime DDL."""

    op.create_table(
        "adk_internal_metadata",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.String(256), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("app_name", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("state", JSONB, nullable=False),
        sa.Column("create_time", sa.DateTime(), nullable=False),
        sa.Column("update_time", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "app_states",
        sa.Column("app_name", sa.String(128), primary_key=True),
        sa.Column("state", JSONB, nullable=False),
        sa.Column("update_time", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "user_states",
        sa.Column("app_name", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("state", JSONB, nullable=False),
        sa.Column("update_time", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("app_name", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("invocation_id", sa.String(256), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("event_data", JSONB, nullable=True),
        sa.ForeignKeyConstraint(
            ["app_name", "user_id", "session_id"],
            ["sessions.app_name", "sessions.user_id", "sessions.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_events_app_user_session_ts",
        "events",
        ["app_name", "user_id", "session_id", sa.text("timestamp DESC")],
    )
    op.execute(
        "INSERT INTO adk_internal_metadata (key, value) VALUES ('schema_version', '1')"
    )
    op.execute(
        "CREATE FUNCTION protect_bound_adk_session() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM repair_phase_sessions "
        "WHERE adk_session_id = OLD.id) THEN "
        "RAISE EXCEPTION 'ADK session is referenced by a retained phase session'; "
        "END IF; RETURN OLD; END; $$"
    )
    op.execute(
        "CREATE TRIGGER trg_protect_bound_adk_session "
        "BEFORE DELETE ON sessions FOR EACH ROW "
        "EXECUTE FUNCTION protect_bound_adk_session()"
    )


def downgrade() -> None:
    """Remove ADK storage only when no app-owned phase mapping references it."""

    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM repair_phase_sessions) THEN "
        "RAISE EXCEPTION 'cannot remove ADK storage while phase sessions "
        "are retained'; "
        "END IF; END $$;"
    )
    op.execute("DROP TRIGGER trg_protect_bound_adk_session ON sessions")
    op.execute("DROP FUNCTION protect_bound_adk_session()")
    op.drop_index("idx_events_app_user_session_ts", table_name="events")
    for table in (
        "events",
        "user_states",
        "app_states",
        "sessions",
        "adk_internal_metadata",
    ):
        op.drop_table(table)
