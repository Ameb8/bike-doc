"""Permit app-owned phase references before diagnostic executor initialization."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("repair_phase_sessions", "adk_session_id", nullable=True)


def downgrade() -> None:
    # Fail safely if accepted references still need initialization; never delete them.
    op.alter_column("repair_phase_sessions", "adk_session_id", nullable=False)
