"""Persist V2 bike-profile projections, claims, and field resolutions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-11 00:02:00.000000+00:00
"""

# ruff: noqa: E501

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the claim ledger and backfill the canonical V2 projection."""
    op.execute(
        """
        ALTER TABLE bike_profiles
          ADD COLUMN profile_revision bigint NOT NULL DEFAULT 0,
          ADD COLUMN technical_profile jsonb NOT NULL DEFAULT
            '{"schema_version":"bike_profile.v2","identity":{},"frame":{},"brakes":{"front":{},"rear":{}},
              "drivetrain":{},"rolling_system":{"front":{},"rear":{}},
              "suspension":{},"cockpit":{},"seating":{},"electric_assist":{}}'::jsonb,
          ADD CONSTRAINT ck_bike_profiles_technical_profile_v2 CHECK (
            jsonb_typeof(technical_profile) = 'object'
            AND technical_profile->>'schema_version' = 'bike_profile.v2'
            AND technical_profile ?& ARRAY[
              'identity', 'frame', 'brakes', 'drivetrain', 'rolling_system',
              'suspension', 'cockpit', 'seating', 'electric_assist'
            ]
          );
        """,
    )

    op.execute(
        """
        CREATE TABLE bike_fact_claims (
          id text PRIMARY KEY,
          bike_id text NOT NULL,
          field_path text NOT NULL,
          value jsonb NULL,
          source_type text NOT NULL,
          source_ref jsonb NOT NULL DEFAULT '{}'::jsonb,
          evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
          scope_assumption text NULL,
          observed_at timestamptz NOT NULL,
          evidence_basis text NULL,
          visibility text NULL,
          model_score double precision NULL,
          evidence_cues jsonb NULL,
          disposition text NOT NULL DEFAULT 'pending',
          disposition_reason text NULL,
          created_at timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT fk_bike_fact_claims_bike
            FOREIGN KEY (bike_id) REFERENCES bike_profiles (id) ON DELETE RESTRICT,
          CONSTRAINT ck_bike_fact_claims_id_prefix CHECK (id LIKE 'bfc_%'),
          CONSTRAINT ck_bike_fact_claims_source_type CHECK (source_type IN (
            'manual_profile_edit', 'manual_profile_clear', 'image_inference',
            'legacy_profile_migration', 'derived_resolution'
          )),
          CONSTRAINT ck_bike_fact_claims_disposition CHECK (disposition IN (
            'pending', 'applied', 'supporting', 'conflict', 'superseded', 'rejected'
          )),
          CONSTRAINT ck_bike_fact_claims_clear_value CHECK (
            (source_type = 'manual_profile_clear') = (value IS NULL)
          )
        );
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_bike_fact_claims_bike_field
          ON bike_fact_claims (bike_id, field_path, created_at);
        """,
    )

    op.execute(
        """
        CREATE FUNCTION prevent_bike_fact_claim_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.bike_id IS DISTINCT FROM OLD.bike_id
            OR NEW.field_path IS DISTINCT FROM OLD.field_path
            OR NEW.value IS DISTINCT FROM OLD.value
            OR NEW.source_type IS DISTINCT FROM OLD.source_type
            OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
            OR NEW.evidence_refs IS DISTINCT FROM OLD.evidence_refs
            OR NEW.scope_assumption IS DISTINCT FROM OLD.scope_assumption
            OR NEW.observed_at IS DISTINCT FROM OLD.observed_at
            OR NEW.evidence_basis IS DISTINCT FROM OLD.evidence_basis
            OR NEW.visibility IS DISTINCT FROM OLD.visibility
            OR NEW.model_score IS DISTINCT FROM OLD.model_score
            OR NEW.evidence_cues IS DISTINCT FROM OLD.evidence_cues
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
          THEN
            RAISE EXCEPTION 'Bike fact claim provenance is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        """,
    )
    op.execute(
        """
        CREATE TRIGGER trg_bike_fact_claims_immutable
        BEFORE UPDATE ON bike_fact_claims
        FOR EACH ROW
        EXECUTE FUNCTION prevent_bike_fact_claim_mutation();
        """,
    )

    op.execute(
        """
        CREATE TABLE bike_field_resolutions (
          bike_id text NOT NULL,
          field_path text NOT NULL,
          current_value jsonb NULL,
          resolution_state text NOT NULL,
          current_claim_id text NULL,
          supporting_claim_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
          conflicting_claim_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
          effective_confidence text NOT NULL DEFAULT 'unknown',
          source_type text NULL,
          observed_at timestamptz NULL,
          resolved_at timestamptz NULL,
          manual_clear_barrier_at timestamptz NULL,

          PRIMARY KEY (bike_id, field_path),
          CONSTRAINT fk_bike_field_resolutions_bike
            FOREIGN KEY (bike_id) REFERENCES bike_profiles (id) ON DELETE RESTRICT,
          CONSTRAINT fk_bike_field_resolutions_claim
            FOREIGN KEY (current_claim_id) REFERENCES bike_fact_claims (id)
              ON DELETE RESTRICT,
          CONSTRAINT ck_bike_field_resolutions_state CHECK (resolution_state IN (
            'unknown', 'resolved', 'disputed', 'cleared'
          )),
          CONSTRAINT ck_bike_field_resolutions_confidence CHECK (
            effective_confidence IN ('unknown', 'low', 'medium', 'high')
          )
        );
        """,
    )
    _migrate_legacy_claims()
    op.execute(
        """
        INSERT INTO bike_field_resolutions (
          bike_id, field_path, current_value, resolution_state, current_claim_id,
          effective_confidence, source_type, observed_at, resolved_at
        )
        SELECT
          bike_id,
          field_path,
          value,
          'resolved',
          id,
          'high',
          source_type,
          observed_at,
          created_at
        FROM bike_fact_claims
        WHERE source_type = 'legacy_profile_migration';
        """,
    )

    op.execute(
        """
        UPDATE bike_profiles AS bike
        SET technical_profile = jsonb_strip_nulls(jsonb_build_object(
          'schema_version', 'bike_profile.v2',
          'identity', jsonb_strip_nulls(jsonb_build_object(
            'make', CASE WHEN lower(btrim(bike.make)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.make), '') END,
            'model', CASE WHEN lower(btrim(bike.model)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.model), '') END,
            'model_year', bike.model_year,
            'bike_type', NULLIF(bike.bike_type, 'unknown')
          )),
          'frame', jsonb_strip_nulls(jsonb_build_object(
            'material', NULLIF(bike.frame_material, 'unknown')
          )),
          'brakes', jsonb_strip_nulls(jsonb_build_object(
            'front', CASE bike.brake_type
              WHEN 'mechanical_disc' THEN '{"mechanism":"disc","actuation":"mechanical"}'::jsonb
              WHEN 'hydraulic_disc' THEN '{"mechanism":"disc","actuation":"hydraulic"}'::jsonb
              WHEN 'rim' THEN '{"mechanism":"rim_other"}'::jsonb
              ELSE NULL
            END,
            'rear', CASE bike.brake_type
              WHEN 'mechanical_disc' THEN '{"mechanism":"disc","actuation":"mechanical"}'::jsonb
              WHEN 'hydraulic_disc' THEN '{"mechanism":"disc","actuation":"hydraulic"}'::jsonb
              WHEN 'rim' THEN '{"mechanism":"rim_other"}'::jsonb
              WHEN 'coaster' THEN '{"mechanism":"coaster","actuation":"none"}'::jsonb
              ELSE NULL
            END,
            'legacy_summary', CASE WHEN bike.brake_type = 'other' THEN '"other"'::jsonb END
          )),
          'drivetrain', jsonb_strip_nulls(jsonb_build_object(
            'legacy_description', CASE WHEN lower(btrim(bike.drivetrain)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.drivetrain), '') END
          )),
          'rolling_system', jsonb_strip_nulls(jsonb_build_object(
            'front', jsonb_strip_nulls(jsonb_build_object(
              'wheel', jsonb_strip_nulls(jsonb_build_object('nominal_size', CASE WHEN lower(btrim(bike.wheel_size)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.wheel_size), '') END)),
              'tire', jsonb_strip_nulls(jsonb_build_object('marked_size', CASE WHEN lower(btrim(bike.tire_size)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.tire_size), '') END))
            )),
            'rear', jsonb_strip_nulls(jsonb_build_object(
              'wheel', jsonb_strip_nulls(jsonb_build_object('nominal_size', CASE WHEN lower(btrim(bike.wheel_size)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.wheel_size), '') END)),
              'tire', jsonb_strip_nulls(jsonb_build_object('marked_size', CASE WHEN lower(btrim(bike.tire_size)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.tire_size), '') END))
            ))
          )),
          'suspension', '{}'::jsonb,
          'cockpit', '{}'::jsonb,
          'seating', '{}'::jsonb,
          'electric_assist', '{}'::jsonb
        )),
        profile_revision = CASE WHEN EXISTS (
          SELECT 1 FROM bike_fact_claims AS claim WHERE claim.bike_id = bike.id
        ) THEN 1 ELSE 0 END;
        """,
    )


def _migrate_legacy_claims() -> None:
    """Create immutable V2 claims without assigning unsupported scope."""
    insert = """
        INSERT INTO bike_fact_claims (
          id, bike_id, field_path, value, source_type, source_ref,
          scope_assumption, observed_at, disposition, disposition_reason, created_at
        )
        SELECT
          'bfc_' || substr(md5(bike.id || ':' || field_path), 1, 26),
          bike.id,
          field_path,
          value,
          'legacy_profile_migration',
          jsonb_build_object('type', 'legacy_bike_profile', 'id', bike.id),
          scope_assumption,
          bike.updated_at,
          'applied',
          'legacy_profile_migration',
          bike.updated_at
        FROM bike_profiles AS bike
        CROSS JOIN LATERAL (
          VALUES {values}
        ) AS migrated(field_path, value, scope_assumption)
        WHERE value IS NOT NULL
    """
    op.execute(
        insert.format(
            values="""
              ('identity.make', to_jsonb(CASE WHEN lower(btrim(bike.make)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.make), '') END), NULL::text),
              ('identity.model', to_jsonb(CASE WHEN lower(btrim(bike.model)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.model), '') END), NULL::text),
              ('identity.model_year', to_jsonb(bike.model_year), NULL::text),
              ('identity.bike_type', CASE WHEN bike.bike_type = 'unknown' THEN NULL ELSE to_jsonb(bike.bike_type) END, NULL::text),
              ('frame.material', CASE WHEN bike.frame_material = 'unknown' THEN NULL ELSE to_jsonb(bike.frame_material) END, NULL::text),
              ('drivetrain.legacy_description', to_jsonb(CASE WHEN lower(btrim(bike.drivetrain)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.drivetrain), '') END), NULL::text),
              ('brakes.front.mechanism', CASE WHEN bike.brake_type IN ('mechanical_disc', 'hydraulic_disc') THEN '"disc"'::jsonb WHEN bike.brake_type = 'rim' THEN '"rim_other"'::jsonb END, CASE WHEN bike.brake_type IN ('mechanical_disc', 'hydraulic_disc', 'rim') THEN 'whole_bike' END),
              ('brakes.front.actuation', CASE WHEN bike.brake_type = 'mechanical_disc' THEN '"mechanical"'::jsonb WHEN bike.brake_type = 'hydraulic_disc' THEN '"hydraulic"'::jsonb END, CASE WHEN bike.brake_type IN ('mechanical_disc', 'hydraulic_disc') THEN 'whole_bike' END),
              ('brakes.rear.mechanism', CASE WHEN bike.brake_type IN ('mechanical_disc', 'hydraulic_disc') THEN '"disc"'::jsonb WHEN bike.brake_type = 'rim' THEN '"rim_other"'::jsonb WHEN bike.brake_type = 'coaster' THEN '"coaster"'::jsonb END, CASE WHEN bike.brake_type IN ('mechanical_disc', 'hydraulic_disc', 'rim') THEN 'whole_bike' END),
              ('brakes.rear.actuation', CASE WHEN bike.brake_type = 'mechanical_disc' THEN '"mechanical"'::jsonb WHEN bike.brake_type = 'hydraulic_disc' THEN '"hydraulic"'::jsonb WHEN bike.brake_type = 'coaster' THEN '"none"'::jsonb END, CASE WHEN bike.brake_type IN ('mechanical_disc', 'hydraulic_disc') THEN 'whole_bike' END),
              ('brakes.legacy_summary', CASE WHEN bike.brake_type = 'other' THEN '"other"'::jsonb END, NULL::text),
              ('rolling_system.front.wheel.nominal_size', to_jsonb(CASE WHEN lower(btrim(bike.wheel_size)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.wheel_size), '') END), CASE WHEN NULLIF(btrim(bike.wheel_size), '') IS NOT NULL AND lower(btrim(bike.wheel_size)) <> 'unknown' THEN 'whole_bike' END),
              ('rolling_system.rear.wheel.nominal_size', to_jsonb(CASE WHEN lower(btrim(bike.wheel_size)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.wheel_size), '') END), CASE WHEN NULLIF(btrim(bike.wheel_size), '') IS NOT NULL AND lower(btrim(bike.wheel_size)) <> 'unknown' THEN 'whole_bike' END),
              ('rolling_system.front.tire.marked_size', to_jsonb(CASE WHEN lower(btrim(bike.tire_size)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.tire_size), '') END), CASE WHEN NULLIF(btrim(bike.tire_size), '') IS NOT NULL AND lower(btrim(bike.tire_size)) <> 'unknown' THEN 'whole_bike' END),
              ('rolling_system.rear.tire.marked_size', to_jsonb(CASE WHEN lower(btrim(bike.tire_size)) = 'unknown' THEN NULL ELSE NULLIF(btrim(bike.tire_size), '') END), CASE WHEN NULLIF(btrim(bike.tire_size), '') IS NOT NULL AND lower(btrim(bike.tire_size)) <> 'unknown' THEN 'whole_bike' END)
            """,
        ),
    )


def downgrade() -> None:
    """Remove V2 profile persistence."""
    op.execute("DROP TABLE bike_field_resolutions;")
    op.execute("DROP TABLE bike_fact_claims;")
    op.execute("DROP FUNCTION prevent_bike_fact_claim_mutation();")
    op.execute(
        "ALTER TABLE bike_profiles "
        "DROP COLUMN technical_profile, "
        "DROP COLUMN profile_revision;"
    )
