"""Retain the identity that crossed the diagnostic effect boundary."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing crossed boundaries without an owner remain fail-closed.
    op.add_column("background_jobs", sa.Column("effect_boundary_token", sa.String(32)))
    op.create_check_constraint(
        "ck_background_jobs_effect_token",
        "background_jobs",
        "effect_boundary_token IS NULL OR (effect_boundary_at IS NOT NULL "
        "AND length(effect_boundary_token) = 32)",
    )
    op.execute("""
        CREATE FUNCTION protect_diagnostic_effect_token() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF OLD.effect_boundary_at IS NOT NULL AND
             NEW.effect_boundary_token IS DISTINCT FROM OLD.effect_boundary_token
          THEN
            RAISE EXCEPTION 'effect boundary owner is immutable'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute(
        "CREATE TRIGGER trg_protect_diagnostic_effect_token BEFORE UPDATE "
        "ON background_jobs FOR EACH ROW "
        "EXECUTE FUNCTION protect_diagnostic_effect_token()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_protect_diagnostic_effect_token ON background_jobs")
    op.execute("DROP FUNCTION protect_diagnostic_effect_token()")
    op.drop_constraint(
        "ck_background_jobs_effect_token", "background_jobs", type_="check"
    )
    op.drop_column("background_jobs", "effect_boundary_token")
