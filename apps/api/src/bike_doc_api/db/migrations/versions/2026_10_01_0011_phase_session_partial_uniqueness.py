"""Index only initialized ADK phase bindings; retain deferred references."""

from alembic import op
from sqlalchemy import text

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0010 already made the column nullable. SQL CHECK permits NULL while
    # continuing to reject non-null blanks; neither constraint changes here.
    op.drop_index("ux_repair_phase_sessions_adk_session", "repair_phase_sessions")
    op.create_index(
        "ux_repair_phase_sessions_adk_session",
        "repair_phase_sessions",
        ["adk_session_id"],
        unique=True,
        postgresql_where=text("adk_session_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Restore 0010 exactly: nullable with the original full unique index.
    # Downgrading 0010 itself refuses retained NULL references rather than
    # deleting them or inventing replacement conversation state.
    op.drop_index("ux_repair_phase_sessions_adk_session", "repair_phase_sessions")
    op.create_index(
        "ux_repair_phase_sessions_adk_session",
        "repair_phase_sessions",
        ["adk_session_id"],
        unique=True,
    )
