"""Snapshot the app-owned diagnostic report rollout per phase session.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-01 12:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an immutable report-version selection for diagnostic sessions."""

    op.execute(
        "ALTER TABLE repair_phase_sessions "
        "ADD COLUMN diagnostic_report_schema_version text NULL;"
    )
    op.execute(
        "UPDATE repair_phase_sessions "
        "SET diagnostic_report_schema_version = 'diagnostic_report.v1' "
        "WHERE phase = 'diagnostic';"
    )
    op.execute(
        "ALTER TABLE repair_phase_sessions ADD CONSTRAINT "
        "ck_repair_phase_sessions_diagnostic_report_schema_version "
        "CHECK (diagnostic_report_schema_version IS NULL OR "
        "diagnostic_report_schema_version IN "
        "('diagnostic_report.v1', 'diagnostic_report.v2'));"
    )
    op.execute(
        "CREATE FUNCTION prevent_diagnostic_report_version_change() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF OLD.diagnostic_report_schema_version IS DISTINCT FROM "
        "NEW.diagnostic_report_schema_version THEN "
        "RAISE EXCEPTION 'diagnostic report version is immutable'; END IF; "
        "RETURN NEW; END; $$;"
    )
    op.execute(
        "CREATE TRIGGER trg_repair_phase_sessions_report_version_immutable "
        "BEFORE UPDATE ON repair_phase_sessions FOR EACH ROW "
        "EXECUTE FUNCTION prevent_diagnostic_report_version_change();"
    )


def downgrade() -> None:
    """Remove the diagnostic report-version snapshot."""

    op.execute(
        "DROP TRIGGER trg_repair_phase_sessions_report_version_immutable "
        "ON repair_phase_sessions;"
    )
    op.execute("DROP FUNCTION prevent_diagnostic_report_version_change();")
    op.execute(
        "ALTER TABLE repair_phase_sessions DROP CONSTRAINT "
        "ck_repair_phase_sessions_diagnostic_report_schema_version;"
    )
    op.execute(
        "ALTER TABLE repair_phase_sessions "
        "DROP COLUMN diagnostic_report_schema_version;"
    )
