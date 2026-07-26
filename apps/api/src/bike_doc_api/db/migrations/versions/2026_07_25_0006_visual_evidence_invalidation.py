"""Make reports ineligible when their source image evidence is inaccessible.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-25 12:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add irreversible report-evidence redaction state and citation index."""

    op.execute(
        "ALTER TABLE phase_reports ADD COLUMN evidence_redacted_at timestamptz NULL;"
    )
    op.execute(
        "ALTER TABLE phase_reports ADD COLUMN evidence_redaction_reason text NULL;"
    )
    op.execute(
        """
        ALTER TABLE phase_reports
        ADD CONSTRAINT ck_phase_reports_evidence_redaction_pair
        CHECK ((evidence_redacted_at IS NULL AND evidence_redaction_reason IS NULL)
          OR (evidence_redacted_at IS NOT NULL
              AND evidence_redaction_reason IS NOT NULL));
        """
    )
    op.execute(
        """
        CREATE INDEX ix_phase_reports_source_artifact_ids_usable
        ON phase_reports USING gin (source_artifact_ids)
        WHERE evidence_redacted_at IS NULL;
        """
    )


def downgrade() -> None:
    """Remove report evidence invalidation metadata."""

    op.execute("DROP INDEX ix_phase_reports_source_artifact_ids_usable;")
    op.execute(
        "ALTER TABLE phase_reports DROP CONSTRAINT "
        "ck_phase_reports_evidence_redaction_pair;"
    )
    op.execute("ALTER TABLE phase_reports DROP COLUMN evidence_redaction_reason;")
    op.execute("ALTER TABLE phase_reports DROP COLUMN evidence_redacted_at;")
