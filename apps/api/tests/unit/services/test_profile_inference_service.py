"""Shadow bike-profile inference service behavior."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from bike_doc_api.core.config import ProfileInferenceFieldPolicySettings
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.bike import BikeFactClaim, BikeFieldResolution, BikeProfile
from bike_doc_api.models.repair_session import RepairSession, RepairTurn
from bike_doc_api.services.profile_inference import (
    ProfileInferenceService,
    ProfileInferenceStatus,
)
from bike_doc_api.services.profile_inference_resolution import (
    ActiveFieldPolicy,
    ProfileResolverPolicy,
)
from bike_doc_api.services.profile_inference_telemetry import (
    RecordingProfileInferenceTelemetry,
)


class _Extractor:
    def __init__(
        self,
        output: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.requests: list[object] = []

    async def extract(self, _request: object) -> dict[str, object]:
        self.requests.append(_request)
        if self.error is not None:
            raise self.error
        return self.output or {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.99,
            },
            "claims": [
                {
                    "field_path": "brakes.rear.mechanism",
                    "value": "disc",
                    "subject_relation": "installed_on_target_bike",
                    "evidence_basis": "direct_visual",
                    "visibility": "clear",
                    "confidence_score": 0.99,
                    "artifact_ids": ["art_rear"],
                    "observed_text": None,
                    "evidence_cues": ["A rotor and rear caliper are visible."],
                },
                {
                    "field_path": "brakes.rear.actuation",
                    "value": "hydraulic",
                    "subject_relation": "installed_on_target_bike",
                    "evidence_basis": "direct_visual",
                    "visibility": "clear",
                    "confidence_score": 0.98,
                    "artifact_ids": ["art_rear"],
                    "observed_text": None,
                    "evidence_cues": ["A hose enters the rear caliper."],
                },
            ],
            "abstentions": [],
        }


class _SequenceExtractor:
    """Deterministic provider fake for controlled retry tests."""

    provider = "fake"

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requests: list[object] = []

    async def extract(self, request: object) -> dict[str, object]:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, dict)
        return outcome


class _Store:
    def __init__(self) -> None:
        now = datetime(2026, 7, 11, tzinfo=UTC)
        self.turn = RepairTurn(
            id="turn_rear",
            repair_session_id="rs_rear",
            repair_phase_session_id="phs_rear",
            client_turn_id="client-rear",
            request_hash="request-hash",
            schema_version="ai_turn.v1",
            phase="diagnostic",
            message={"text": "This is the rear brake.", "artifact_ids": ["art_rear"]},
            start_event_sequence=1,
            created_at=now,
        )
        self.session = RepairSession(
            id="rs_rear",
            user_id="usr_rear",
            bike_id="bike_rear",
            phase="diagnostic",
            status="running",
            safety_state="ok",
            current_input_request=None,
            execution_progress=None,
            active_safety_flags=[],
            latest_event_sequence=1,
            created_at=now,
            updated_at=now,
        )
        self.bike = BikeProfile(
            id="bike_rear",
            user_id="usr_rear",
            display_name="Rear brake bike",
            bike_type="unknown",
            frame_material="unknown",
            brake_type="unknown",
            technical_profile={
                "identity": {},
                "frame": {},
                "brakes": {"front": {}, "rear": {}},
                "drivetrain": {},
                "rolling_system": {"front": {}, "rear": {}},
                "suspension": {},
                "cockpit": {},
                "seating": {},
                "electric_assist": {},
            },
            profile_revision=0,
            created_at=now,
            updated_at=now,
        )
        self.artifact = ArtifactRef(
            id="art_rear",
            user_id="usr_rear",
            repair_session_id="rs_rear",
            purpose="diagnostic_photo",
            media_type="image",
            mime_type="image/jpeg",
            filename="rear.jpg",
            byte_size=3,
            status="ready",
            content_sha256="a" * 64,
            storage_provider="fake",
            storage_path="rear.jpg",
            created_at=now,
            updated_at=now,
        )
        self.artifacts = {self.artifact.id: self.artifact}
        self.storage_error: Exception | None = None
        self.claims: list[object] = []
        self.runs: list[object] = []
        self.resolutions: dict[tuple[str, str], object] = {}

    async def get(self, identifier: str) -> object | None:
        if identifier == self.turn.id:
            return self.turn
        if identifier == self.session.id:
            return self.session
        return None

    async def get_owned_active(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfile | None:
        if self.bike.id == bike_id and self.bike.user_id == user_id:
            return self.bike
        return None

    async def get_owned_active_for_update(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfile | None:
        return await self.get_owned_active(bike_id=bike_id, user_id=user_id)

    async def get_resolution(
        self,
        *,
        bike_id: str,
        field_path: str,
    ) -> object | None:
        return self.resolutions.get((bike_id, field_path))

    async def save_resolution(self, resolution: object) -> object:
        if getattr(self, "fail_resolution_for", None) == resolution.field_path:
            raise RuntimeError("resolution write failed")
        self.resolutions[(resolution.bike_id, resolution.field_path)] = resolution
        return resolution

    async def get_owned(self, *, artifact_id: str, user_id: str) -> ArtifactRef | None:
        artifact = self.artifacts.get(artifact_id)
        if artifact is not None and artifact.user_id == user_id:
            return artifact
        return None

    async def get_by_identity(
        self,
        *,
        turn_id: str,
        inference_schema_version: str,
        extractor_version: str,
    ) -> object | None:
        for run in self.runs:
            if (
                run.turn_id == turn_id
                and run.inference_schema_version == inference_schema_version
                and run.extractor_version == extractor_version
            ):
                return run
        return None

    async def add(self, item: object) -> object:
        if hasattr(item, "field_path"):
            self.claims.append(item)
        else:
            self.runs.append(item)
        return item

    async def save(self, item: object) -> object:
        return item

    async def add_claim(self, claim: object) -> object:
        self.claims.append(claim)
        return claim

    async def get_object(self, *, path: str, bucket: str | None) -> bytes:
        if self.storage_error is not None:
            raise self.storage_error
        assert path in {"rear.jpg", "rear-second.jpg"}
        assert bucket is None
        return b"jpg"


async def test_rear_brake_claims_are_persisted_as_pending_shadow_evidence() -> None:
    store = _Store()
    extractor = _Extractor()
    service = ProfileInferenceService(
        turns=store,
        repair_sessions=store,
        bikes=store,
        artifacts=store,
        runs=store,
        storage=store,
        extractor=extractor,
        extractor_version="rear-brake-shadow.v1",
    )

    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert outcome.claim_count == 2
    assert len(store.runs) == 1
    assert [claim.field_path for claim in store.claims] == [
        "brakes.rear.mechanism",
        "brakes.rear.actuation",
    ]
    assert {claim.disposition for claim in store.claims} == {"pending"}
    assert {claim.source_type for claim in store.claims} == {"image_inference"}
    assert {claim.scope_assumption for claim in store.claims} == {
        "installed_on_target_bike"
    }
    assert store.bike.technical_profile["brakes"]["rear"] == {}
    assert store.bike.profile_revision == 0
    request = extractor.requests[0]
    assert request.images[0].artifact_id == "art_rear"
    assert request.caption == "This is the rear brake."
    assert not hasattr(request, "profile")
    assert not hasattr(request, "diagnostic_history")


async def test_explicit_shadow_policy_marks_outcome_shadow_and_never_mutates() -> None:
    store = _Store()

    outcome = await _service(
        store,
        _Extractor(),
        policy=ProfileResolverPolicy.shadow(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.policy_mode == "shadow"
    assert {claim.disposition_reason for claim in store.claims} == {"shadow_policy"}
    assert store.bike.profile_revision == 0
    assert store.bike.technical_profile["brakes"]["rear"] == {}


async def test_bootstrap_policy_auto_fills_clear_installed_rear_hydraulic_disc() -> (
    None
):
    store = _Store()
    service = _service(store, _Extractor(), policy=ProfileResolverPolicy.bootstrap_v1())

    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert outcome.policy_mode == "provisional"
    assert store.bike.technical_profile["brakes"]["rear"] == {
        "mechanism": "disc",
        "actuation": "hydraulic",
    }
    assert store.bike.profile_revision == 1
    assert {claim.disposition for claim in store.claims} == {"applied"}


async def test_bootstrap_policy_resolves_installed_derailleur_topology() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim("drivetrain.architecture", "derailleur"),
        _drivetrain_claim("drivetrain.drive_medium", "chain"),
        _drivetrain_claim("drivetrain.rear_shifter.presence", "present"),
        _drivetrain_claim("drivetrain.front_shifter.presence", "present"),
        _drivetrain_claim("drivetrain.front_derailleur.presence", "absent"),
        _drivetrain_claim("drivetrain.rear_derailleur.presence", "present"),
        _drivetrain_claim("drivetrain.crankset.presence", "present"),
        _drivetrain_claim("drivetrain.rear_cluster.presence", "present"),
        _drivetrain_claim("drivetrain.chain.presence", "present"),
        _drivetrain_claim("drivetrain.belt.presence", "absent"),
        _drivetrain_claim("drivetrain.gear_unit.presence", "absent"),
        _drivetrain_claim("drivetrain.bottom_bracket.presence", "present"),
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert store.bike.technical_profile["drivetrain"] == {
        "architecture": "derailleur",
        "drive_medium": "chain",
        "front_shifter": {"presence": "present"},
        "rear_shifter": {"presence": "present"},
        "front_derailleur": {"presence": "absent"},
        "rear_derailleur": {"presence": "present"},
        "crankset": {"presence": "present"},
        "rear_cluster": {"presence": "present"},
        "chain": {"presence": "present"},
        "belt": {"presence": "absent"},
        "gear_unit": {"presence": "absent"},
        "bottom_bracket": {"presence": "present"},
    }
    assert {claim.disposition for claim in store.claims} == {"applied"}


async def test_bootstrap_policy_resolves_installed_drivetrain_roles_and_identity() -> (
    None
):
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim("drivetrain.front_shifter.presence", "present"),
        _drivetrain_configuration_claim(
            "drivetrain.front_shifter.actuation", "mechanical"
        ),
        _drivetrain_identity_claim("drivetrain.front_shifter.manufacturer", "Shimano"),
        _drivetrain_identity_claim("drivetrain.front_shifter.model", "105"),
        _drivetrain_claim("drivetrain.rear_shifter.presence", "present"),
        _drivetrain_configuration_claim(
            "drivetrain.rear_shifter.actuation", "electronic"
        ),
        _drivetrain_identity_claim("drivetrain.rear_shifter.manufacturer", "SRAM"),
        _drivetrain_identity_claim("drivetrain.rear_shifter.model", "Rival AXS"),
        _drivetrain_claim("drivetrain.rear_derailleur.presence", "present"),
        _drivetrain_configuration_claim(
            "drivetrain.rear_derailleur.mount_type", "direct_mount"
        ),
        _drivetrain_claim("drivetrain.rear_cluster.presence", "present"),
        _drivetrain_configuration_claim(
            "drivetrain.rear_cluster.cluster_type", "cassette"
        ),
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert store.bike.technical_profile["drivetrain"] == {
        "front_shifter": {
            "presence": "present",
            "actuation": "mechanical",
            "manufacturer": "Shimano",
            "model": "105",
        },
        "rear_shifter": {
            "presence": "present",
            "actuation": "electronic",
            "manufacturer": "SRAM",
            "model": "Rival AXS",
        },
        "rear_derailleur": {
            "presence": "present",
            "mount_type": "direct_mount",
        },
        "rear_cluster": {
            "presence": "present",
            "cluster_type": "cassette",
        },
    }
    assert {claim.disposition for claim in store.claims} == {"applied"}


async def test_absent_drivetrain_component_clears_existing_identity_and_specs() -> None:
    store = _Store()
    store.bike.technical_profile["drivetrain"] = {
        "front_derailleur": {
            "presence": "present",
            "manufacturer": "Shimano",
            "model": "105",
        },
    }
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim("drivetrain.front_derailleur.presence", "absent"),
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["drivetrain"]["front_derailleur"] == {
        "presence": "absent",
    }


async def test_absent_drivetrain_component_keeps_same_run_leaves_cleared() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim("drivetrain.rear_derailleur.presence", "absent"),
        _drivetrain_identity_claim("drivetrain.rear_derailleur.manufacturer", "SRAM"),
        _drivetrain_configuration_claim(
            "drivetrain.rear_derailleur.mount_type", "full_mount"
        ),
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["drivetrain"]["rear_derailleur"] == {
        "presence": "absent",
    }
    assert {claim.field_path: claim.disposition_reason for claim in store.claims} == {
        "drivetrain.rear_derailleur.presence": "auto_fill_policy_satisfied",
        "drivetrain.rear_derailleur.manufacturer": "component_is_resolved_absent",
        "drivetrain.rear_derailleur.mount_type": "component_is_resolved_absent",
    }


async def test_unreadable_drivetrain_identity_remains_pending() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        {
            **_drivetrain_claim("drivetrain.rear_shifter.manufacturer", "Shimano"),
            "evidence_basis": "derived_visual",
            "evidence_cues": ["The component resembles a Shimano shifter."],
        },
        {
            **_drivetrain_claim("drivetrain.rear_shifter.model", "105"),
            "evidence_basis": "derived_visual",
            "evidence_cues": ["The component resembles a 105 shifter."],
        },
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["drivetrain"] == {}
    assert {claim.disposition_reason for claim in store.claims} == {
        "no_active_field_policy"
    }


@pytest.mark.parametrize("cluster_type", ["cassette", "freewheel", "single_sprocket"])
async def test_installed_rear_cluster_classification_is_resolved(
    cluster_type: str,
) -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim("drivetrain.rear_cluster.presence", "present"),
        _drivetrain_configuration_claim(
            "drivetrain.rear_cluster.cluster_type", cluster_type
        ),
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["drivetrain"]["rear_cluster"] == {
        "presence": "present",
        "cluster_type": cluster_type,
    }


async def test_bootstrap_policy_resolves_belt_internal_gear_topology() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim("drivetrain.architecture", "internal_gear_hub"),
        _drivetrain_claim("drivetrain.drive_medium", "belt"),
        _drivetrain_claim("drivetrain.front_shifter.presence", "absent"),
        _drivetrain_claim("drivetrain.rear_shifter.presence", "present"),
        _drivetrain_claim("drivetrain.front_derailleur.presence", "absent"),
        _drivetrain_claim("drivetrain.rear_derailleur.presence", "absent"),
        _drivetrain_claim("drivetrain.crankset.presence", "present"),
        _drivetrain_claim("drivetrain.rear_cluster.presence", "absent"),
        _drivetrain_claim("drivetrain.chain.presence", "absent"),
        _drivetrain_claim("drivetrain.belt.presence", "present"),
        _drivetrain_claim("drivetrain.gear_unit.presence", "present"),
        _drivetrain_claim("drivetrain.bottom_bracket.presence", "present"),
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["drivetrain"]["architecture"] == (
        "internal_gear_hub"
    )
    assert store.bike.technical_profile["drivetrain"]["drive_medium"] == "belt"
    assert store.bike.technical_profile["drivetrain"]["chain"] == {
        "presence": "absent",
    }
    assert store.bike.technical_profile["drivetrain"]["belt"] == {
        "presence": "present",
    }
    assert store.bike.technical_profile["drivetrain"]["gear_unit"] == {
        "presence": "present",
    }


@pytest.mark.parametrize(
    ("subject_relation", "visibility"),
    [
        ("loose_component", "clear"),
        ("packaging_or_reference", "clear"),
        ("installed_on_target_bike", "partial"),
    ],
)
async def test_non_clear_or_non_installed_drivetrain_claim_does_not_mutate_profile(
    subject_relation: str,
    visibility: str,
) -> None:
    store = _Store()
    output = _valid_single_claim_output()
    claim = _drivetrain_claim("drivetrain.drive_medium", "chain")
    claim["subject_relation"] = subject_relation
    claim["visibility"] = visibility
    output["scene"]["target_relation"] = subject_relation
    output["claims"] = [claim]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert store.bike.technical_profile["drivetrain"] == {}
    assert store.claims[0].disposition == "pending"


async def test_null_drivetrain_presence_is_rejected_instead_of_meaning_absent() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim("drivetrain.front_derailleur.presence", None),
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.TERMINAL_FAILURE
    assert store.claims == []
    assert store.bike.technical_profile["drivetrain"] == {}


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("drivetrain.front_chainring_count", 2),
        ("drivetrain.rear_speed_count", 10),
        ("drivetrain.legacy_description", "Shimano 105"),
    ],
)
async def test_derived_and_legacy_drivetrain_fields_are_not_inference_targets(
    field_path: str,
    value: object,
) -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim(field_path, value),
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.TERMINAL_FAILURE
    assert store.claims == []
    assert store.bike.technical_profile["drivetrain"] == {}


async def test_resolved_crankset_count_derives_front_chainring_count() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        {
            **_drivetrain_claim("drivetrain.crankset.chainring_count", 2),
            "evidence_basis": "counted_visual",
            "evidence_cues": ["Two installed chainrings are clearly counted."],
        },
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["drivetrain"] == {
        "crankset": {"chainring_count": 2},
        "front_chainring_count": 2,
    }
    assert store.resolutions[
        ("bike_rear", "drivetrain.front_chainring_count")
    ].source_type == ("derived_resolution")


async def test_apparent_drivetrain_count_is_rejected_before_claim_persistence() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _drivetrain_claim("drivetrain.rear_cluster.speed_count", 10),
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.TERMINAL_FAILURE
    assert store.claims == []
    assert store.bike.technical_profile["drivetrain"] == {}


async def test_disagreeing_resolved_rear_count_sources_leave_aggregate_disputed() -> (
    None
):
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        {
            **_drivetrain_claim("drivetrain.rear_cluster.speed_count", 10),
            "evidence_basis": "counted_visual",
            "evidence_cues": ["Ten installed cassette sprockets are counted."],
        },
        {
            **_drivetrain_claim("drivetrain.rear_shifter.speed_count", 11),
            "evidence_basis": "readable_marking",
            "observed_text": "11-speed",
            "evidence_cues": ["The installed shifter's 11-speed marking is readable."],
        },
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["drivetrain"]["rear_speed_count"] is None
    resolution = store.resolutions[("bike_rear", "drivetrain.rear_speed_count")]
    assert resolution.resolution_state == "disputed"
    assert resolution.source_type == "derived_resolution"


async def test_bootstrap_policy_resolves_a_front_brake_without_populating_rear() -> (
    None
):
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"][0]["field_path"] = "brakes.front.mechanism"
    output["claims"][0]["value"] = "rim_v_brake"

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["brakes"]["front"] == {
        "mechanism": "rim_v_brake",
    }
    assert store.bike.technical_profile["brakes"]["rear"] == {}


async def test_readable_rear_tire_sidewall_resolves_only_rear_tire_fields() -> None:
    """A positioned sidewall never supplies wheel, rim, or opposite-tire facts."""

    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _rolling_claim("rolling_system.rear.tire.manufacturer", "Maxxis"),
        _rolling_claim("rolling_system.rear.tire.model", "Minion DHF"),
        _rolling_claim("rolling_system.rear.tire.marked_size", "29 x 2.40"),
        _rolling_claim("rolling_system.rear.tire.iso_width_mm", 61),
        _rolling_claim("rolling_system.rear.tire.iso_bsd_mm", 622),
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert store.bike.technical_profile["rolling_system"] == {
        "front": {},
        "rear": {
            "tire": {
                "manufacturer": "Maxxis",
                "model": "Minion DHF",
                "marked_size": "29 x 2.40",
                "iso_width_mm": 61,
                "iso_bsd_mm": 622,
            },
        },
    }


async def test_tubeless_ready_is_resolved_without_deriving_tubeless_setup() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _rolling_claim("rolling_system.rear.tire.tubeless_ready", True),
        {
            **_rolling_claim("rolling_system.rear.tire.setup", "tubeless"),
            "evidence_basis": "direct_visual",
            "observed_text": None,
        },
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert store.bike.technical_profile["rolling_system"]["rear"]["tire"] == {
        "tubeless_ready": True,
    }
    setup_claim = next(
        claim
        for claim in store.claims
        if claim.field_path == "rolling_system.rear.tire.setup"
    )
    assert setup_claim.disposition == "pending"
    assert setup_claim.disposition_reason == "no_active_field_policy"


async def test_readable_rear_hub_and_wheel_markings_resolve_without_front_leakage() -> (
    None
):
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _rolling_claim("rolling_system.rear.wheel.nominal_size", "29 in"),
        _rolling_claim("rolling_system.rear.wheel.iso_bsd_mm", 622),
        _rolling_claim("rolling_system.rear.rim.internal_width_mm", 30),
        _rolling_claim("rolling_system.rear.hub.manufacturer", "DT Swiss"),
        _rolling_claim("rolling_system.rear.hub.model", "350"),
        _direct_rolling_claim("rolling_system.rear.hub.axle_type", "thru_axle"),
        _rolling_claim("rolling_system.rear.hub.axle_standard", "12x148"),
        _direct_rolling_claim("rolling_system.rear.hub.rotor_mount", "six_bolt"),
        _rolling_claim("rolling_system.rear.hub.driver_interface", "hg"),
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert store.bike.technical_profile["rolling_system"] == {
        "front": {},
        "rear": {
            "wheel": {"nominal_size": "29 in", "iso_bsd_mm": 622},
            "rim": {"internal_width_mm": 30},
            "hub": {
                "manufacturer": "DT Swiss",
                "model": "350",
                "axle_type": "thru_axle",
                "axle_standard": "12x148",
                "rotor_mount": "six_bolt",
                "driver_interface": "hg",
            },
        },
    }


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("rolling_system.front.hub.driver_interface", "hg"),
        ("rolling_system.rear.hub.driver_interface", "hg"),
        ("rolling_system.rear.wheel.nominal_size", "29 in"),
    ],
)
async def test_rolling_claims_without_permitted_direct_evidence_fail_validation(
    field_path: str,
    value: object,
) -> None:
    """Front-only driver and apparent-scale dimensions cannot mutate a profile."""

    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        {
            **_rolling_claim(field_path, value),
            "evidence_basis": "direct_visual",
            "observed_text": None,
            "evidence_cues": ["Shimano drivetrain branding is visible."],
        },
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.TERMINAL_FAILURE
    assert store.claims == []
    assert store.bike.technical_profile["rolling_system"] == {
        "front": {},
        "rear": {},
    }


async def test_marking_based_rolling_claim_requires_observed_text() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        {
            **_rolling_claim("rolling_system.rear.rim.internal_width_mm", 30),
            "observed_text": None,
        },
    ]

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.TERMINAL_FAILURE
    assert store.claims == []


async def test_bootstrap_policy_resolves_installed_brake_component_roles() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _brake_claim("brakes.rear.mechanism", "disc", "direct_visual"),
        _brake_claim("brakes.rear.control.presence", "present", "direct_visual"),
        _brake_claim("brakes.rear.control.manufacturer", "Shimano", "readable_marking"),
        _brake_claim("brakes.rear.brake_unit.presence", "present", "direct_visual"),
        _brake_claim(
            "brakes.rear.brake_unit.mount_standard", "flat_mount", "direct_visual"
        ),
        _brake_claim("brakes.rear.brake_unit.pad_family", "K03S", "readable_marking"),
        _brake_claim("brakes.rear.rotor.presence", "present", "direct_visual"),
        _brake_claim("brakes.rear.rotor.diameter_mm", 160, "readable_marking"),
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["brakes"]["rear"] == {
        "mechanism": "disc",
        "control": {"presence": "present", "manufacturer": "Shimano"},
        "brake_unit": {
            "presence": "present",
            "mount_standard": "flat_mount",
            "pad_family": "K03S",
        },
        "rotor": {"presence": "present", "diameter_mm": 160},
    }


async def test_non_disc_resolution_retires_current_rotor_resolution() -> None:
    store = _Store()
    observed_at = datetime(2026, 7, 10, tzinfo=UTC)
    rotor_claim = BikeFactClaim(
        id="bfc_rotor",
        bike_id=store.bike.id,
        field_path="brakes.front.rotor.presence",
        value="present",
        source_type="manual_profile_edit",
        source_ref={"type": "bike_profile", "id": store.bike.id},
        evidence_refs=[],
        observed_at=observed_at,
        disposition="applied",
    )
    store.claims.append(rotor_claim)
    store.resolutions[(store.bike.id, rotor_claim.field_path)] = BikeFieldResolution(
        bike_id=store.bike.id,
        field_path=rotor_claim.field_path,
        current_value="present",
        resolution_state="resolved",
        current_claim_id=rotor_claim.id,
        effective_confidence="high",
        source_type="manual_profile_edit",
        observed_at=observed_at,
        resolved_at=observed_at,
    )
    store.bike.technical_profile["brakes"]["front"] = {
        "mechanism": "disc",
        "rotor": {"presence": "present"},
    }
    output = _valid_single_claim_output()
    output["claims"][0]["field_path"] = "brakes.front.mechanism"
    output["claims"][0]["value"] = "rim_caliper"

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    rotor_resolution = store.resolutions[(store.bike.id, rotor_claim.field_path)]
    assert store.bike.technical_profile["brakes"]["front"]["rotor"] == {}
    assert rotor_claim.disposition == "superseded"
    assert rotor_resolution.current_value is None
    assert rotor_resolution.resolution_state == "unknown"


async def test_visual_similarity_and_apparent_scale_stay_pending() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"] = [
        _brake_claim("brakes.rear.mechanism", "disc", "direct_visual"),
        _brake_claim("brakes.rear.brake_unit.model", "BR-MT200", "derived_visual"),
        _brake_claim(
            "brakes.rear.brake_unit.mount_standard",
            "flat_mount",
            "derived_visual",
        ),
        _brake_claim("brakes.rear.rotor.diameter_mm", 160, "derived_visual"),
    ]

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["brakes"]["rear"] == {"mechanism": "disc"}
    assert [claim.disposition for claim in store.claims] == [
        "applied",
        "pending",
        "pending",
        "pending",
    ]


async def test_rotor_facts_stay_pending_until_that_end_resolves_as_disc() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"][0]["field_path"] = "brakes.front.rotor.presence"
    output["claims"][0]["value"] = "present"

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.claims[0].disposition == "pending"
    assert store.claims[0].disposition_reason == "disc_mechanism_not_resolved"
    assert store.bike.technical_profile["brakes"]["front"] == {}


async def test_front_coaster_claim_is_rejected_before_profile_mutation() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"][0]["field_path"] = "brakes.front.mechanism"
    output["claims"][0]["value"] = "coaster"

    outcome = await _service(
        store, _Extractor(output)
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.TERMINAL_FAILURE
    assert store.claims == []
    assert store.bike.technical_profile["brakes"]["front"] == {}


async def test_newer_installed_evidence_supersedes_older_manual_rear_brake() -> None:
    store = _Store()
    older = datetime(2026, 7, 10, tzinfo=UTC)
    current = BikeFactClaim(
        id="bfc_manual",
        bike_id=store.bike.id,
        field_path="brakes.rear.actuation",
        value="mechanical",
        source_type="manual_profile_edit",
        source_ref={"type": "bike_profile", "id": store.bike.id},
        evidence_refs=[],
        observed_at=older,
        disposition="applied",
    )
    store.claims.append(current)
    store.resolutions[(store.bike.id, current.field_path)] = BikeFieldResolution(
        bike_id=store.bike.id,
        field_path=current.field_path,
        current_value=current.value,
        resolution_state="resolved",
        current_claim_id=current.id,
        effective_confidence="high",
        source_type=current.source_type,
        observed_at=older,
        resolved_at=older,
    )
    store.turn.created_at = datetime(2026, 7, 11, tzinfo=UTC)

    output = _valid_single_claim_output()
    output["claims"][0]["field_path"] = "brakes.rear.actuation"
    output["claims"][0]["value"] = "hydraulic"
    output["claims"][0]["confidence_score"] = 0.99

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.bike.technical_profile["brakes"]["rear"]["actuation"] == "hydraulic"
    assert store.bike.profile_revision == 1
    assert current.disposition == "superseded"
    assert store.claims[-1].disposition == "applied"


async def test_older_disagreement_is_retained_as_a_disputed_conflict() -> None:
    store = _Store()
    current_time = datetime(2026, 7, 12, tzinfo=UTC)
    current = BikeFactClaim(
        id="bfc_current",
        bike_id=store.bike.id,
        field_path="brakes.rear.actuation",
        value="mechanical",
        source_type="manual_profile_edit",
        source_ref={"type": "bike_profile", "id": store.bike.id},
        evidence_refs=[],
        observed_at=current_time,
        disposition="applied",
    )
    store.claims.append(current)
    store.resolutions[(store.bike.id, current.field_path)] = BikeFieldResolution(
        bike_id=store.bike.id,
        field_path=current.field_path,
        current_value=current.value,
        resolution_state="resolved",
        current_claim_id=current.id,
        effective_confidence="high",
        source_type=current.source_type,
        observed_at=current_time,
        resolved_at=current_time,
    )
    output = _valid_single_claim_output()
    output["claims"][0]["field_path"] = "brakes.rear.actuation"
    output["claims"][0]["value"] = "hydraulic"

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    resolution = store.resolutions[(store.bike.id, current.field_path)]
    assert store.claims[-1].disposition == "conflict"
    assert resolution.current_value == "mechanical"
    assert resolution.resolution_state == "disputed"
    assert resolution.conflicting_claim_ids == [store.claims[-1].id]
    assert store.bike.profile_revision == 1


async def test_evidence_before_manual_clear_remains_pending() -> None:
    store = _Store()
    barrier = datetime(2026, 7, 12, tzinfo=UTC)
    store.resolutions["bike_rear", "brakes.rear.actuation"] = BikeFieldResolution(
        bike_id=store.bike.id,
        field_path="brakes.rear.actuation",
        current_value=None,
        resolution_state="cleared",
        effective_confidence="unknown",
        manual_clear_barrier_at=barrier,
    )
    output = _valid_single_claim_output()
    output["claims"] = [
        claim
        for claim in output["claims"]
        if claim["field_path"] == "brakes.rear.mechanism"
    ]
    output["claims"][0]["field_path"] = "brakes.rear.actuation"
    output["claims"][0]["value"] = "hydraulic"

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    claim = next(
        claim for claim in store.claims if claim.field_path == "brakes.rear.actuation"
    )
    assert claim.disposition == "pending"
    assert claim.disposition_reason == "observed_before_manual_clear_barrier"
    assert (
        store.resolutions["bike_rear", "brakes.rear.actuation"].resolution_state
        == "cleared"
    )
    assert store.bike.profile_revision == 0


async def test_loose_replacement_evidence_is_retained_pending_without_mutation() -> (
    None
):
    store = _Store()
    output = _valid_single_claim_output()
    output["scene"]["target_relation"] = "loose_component"
    output["claims"][0]["subject_relation"] = "loose_component"

    outcome = await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert store.claims[0].disposition == "pending"
    assert store.claims[0].disposition_reason == "not_installed_on_target_bike"
    assert store.bike.technical_profile["brakes"]["rear"] == {}
    assert store.bike.profile_revision == 0


async def test_production_without_promoted_policy_keeps_high_score_claims_pending() -> (
    None
):
    store = _Store()

    outcome = await _service(store, _Extractor()).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.policy_mode == "evaluated"
    assert {claim.disposition_reason for claim in store.claims} == {
        "no_active_field_policy"
    }
    assert store.bike.technical_profile["brakes"]["rear"] == {}
    assert store.bike.profile_revision == 0


async def test_evaluated_production_policy_can_enable_one_promoted_field_class() -> (
    None
):
    store = _Store()
    output = _valid_single_claim_output()
    policy = ProfileResolverPolicy.evaluated(
        {
            ("brakes.rear.mechanism", "direct_visual"): ActiveFieldPolicy(
                auto_fill_threshold=0.96,
            ),
        },
    )

    outcome = await _service(
        store,
        _Extractor(output),
        policy=policy,
    ).process_submitted_profile_evidence("turn_rear")

    assert outcome.policy_mode == "evaluated"
    assert store.claims[0].disposition == "applied"
    resolution = store.resolutions[("bike_rear", "brakes.rear.mechanism")]
    assert resolution.effective_confidence == "medium"


async def test_evaluated_deployment_promotes_only_field_classes_with_passed_gates() -> (
    None
):
    store = _Store()
    policy = ProfileResolverPolicy.from_deployment(
        mode="evaluated",
        policies=[
            ProfileInferenceFieldPolicySettings(
                field_path="brakes.rear.mechanism",
                evidence_class="direct_visual",
                calibration_key="rear-brake-mechanism.v1",
                policy_version="rear-brake-policy.v1",
                auto_fill_threshold=0.96,
                precision_gate_passed=True,
                accepted_baseline_version="rear-brake-v1.0.0",
                regression_evidence_passed=True,
                promoted=True,
            ),
            ProfileInferenceFieldPolicySettings(
                field_path="brakes.rear.actuation",
                evidence_class="direct_visual",
                calibration_key="rear-brake-actuation.v1",
                policy_version="rear-brake-policy.v1",
                auto_fill_threshold=0.96,
                precision_gate_passed=False,
                promoted=True,
            ),
        ],
    )

    await _service(
        store, _Extractor(), policy=policy
    ).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert store.claims[0].disposition == "applied"
    assert store.claims[1].disposition == "pending"
    assert store.claims[1].disposition_reason == "no_active_field_policy"


@pytest.mark.parametrize(
    ("score", "expected_state", "expected_confidence"),
    [
        (0.919, "pending", None),
        (0.92, "applied", "medium"),
        (0.97, "applied", "high"),
    ],
)
async def test_bootstrap_maps_raw_score_only_through_inclusive_field_policy(
    score: float,
    expected_state: str,
    expected_confidence: str | None,
) -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"][0]["confidence_score"] = score

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.claims[0].disposition == expected_state
    if expected_confidence is None:
        assert store.bike.profile_revision == 0
        assert "brakes.rear.mechanism" not in store.resolutions
    else:
        resolution = store.resolutions[("bike_rear", "brakes.rear.mechanism")]
        assert resolution.effective_confidence == expected_confidence
        assert not hasattr(resolution, "model_score")


async def test_bootstrap_keeps_partial_installed_evidence_pending() -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"][0]["visibility"] = "partial"

    await _service(
        store,
        _Extractor(output),
        policy=ProfileResolverPolicy.bootstrap_v1(),
    ).process_submitted_profile_evidence("turn_rear")

    assert store.claims[0].disposition == "pending"
    assert store.claims[0].disposition_reason == "visibility_not_clear"
    assert store.bike.profile_revision == 0


async def test_resolution_failure_rolls_back_profile_changes() -> None:
    store = _Store()
    store.fail_resolution_for = "brakes.rear.actuation"
    committed: dict[str, object] = {}
    rollbacks = 0

    async def commit() -> None:
        committed["claims"] = list(store.claims)
        committed["resolutions"] = dict(store.resolutions)
        committed["technical_profile"] = deepcopy(store.bike.technical_profile)
        committed["profile_revision"] = store.bike.profile_revision

    async def rollback() -> None:
        nonlocal rollbacks
        rollbacks += 1
        store.claims = list(committed["claims"])
        store.resolutions = dict(committed["resolutions"])
        store.bike.technical_profile = deepcopy(committed["technical_profile"])
        store.bike.profile_revision = committed["profile_revision"]

    service = ProfileInferenceService(
        turns=store,
        repair_sessions=store,
        bikes=store,
        artifacts=store,
        runs=store,
        storage=store,
        extractor=_Extractor(),
        extractor_version="rear-brake-shadow.v1",
        resolver_policy=ProfileResolverPolicy.bootstrap_v1(),
        commit=commit,
        rollback=rollback,
    )

    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert rollbacks == 3
    assert outcome.status is ProfileInferenceStatus.EXHAUSTED
    assert store.claims == []
    assert store.resolutions == {}
    assert store.bike.technical_profile["brakes"]["rear"] == {}
    assert store.bike.profile_revision == 0


async def test_explicit_abstention_completes_without_claims() -> None:
    store = _Store()
    extractor = _Extractor(
        {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.9,
            },
            "claims": [],
            "abstentions": [
                {"field_path": "brakes.rear.actuation", "reason": "not_visible"},
            ],
        },
    )
    service = _service(store, extractor)

    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.ABSTAINED
    assert store.claims == []
    assert store.runs[0].status == "abstained"


async def test_schema_invalid_output_fails_without_claims() -> None:
    store = _Store()
    extractor = _Extractor(
        {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.9,
                "unexpected": "field",
            },
            "claims": [],
            "abstentions": [],
        },
    )

    outcome = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.FAILED
    assert store.runs[0].failure_code == "schema_invalid"
    assert store.claims == []


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"field_path": "brakes.rear.unknown"},
        {"artifact_ids": ["art_not_in_turn"]},
    ],
)
async def test_invalid_scope_unknown_path_or_artifact_reference_fails_run(
    claim_overrides: dict[str, object],
) -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"][0].update(claim_overrides)

    service = _service(store, _Extractor(output))
    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.FAILED
    assert store.runs[0].failure_code == "schema_invalid"
    assert store.claims == []


@pytest.mark.parametrize(
    ("scene_overrides", "subject_relation"),
    [
        ({"contains_bicycle": False}, "installed_on_target_bike"),
        ({"multiple_bicycles": True}, "installed_on_target_bike"),
        ({"target_relation": "other_bike"}, "installed_on_target_bike"),
        ({}, "loose_component"),
    ],
)
async def test_scene_or_subject_that_cannot_prove_target_installed_brake_fails_run(
    scene_overrides: dict[str, object],
    subject_relation: str,
) -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["scene"].update(scene_overrides)
    output["claims"][0]["subject_relation"] = subject_relation

    outcome = await _service(
        store, _Extractor(output)
    ).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.FAILED
    assert store.runs[0].failure_code == "schema_invalid"
    assert store.claims == []


async def test_unavailable_artifact_is_retryable_without_provider_access() -> None:
    store = _Store()
    store.artifact.status = "processing"
    extractor = _Extractor()

    outcome = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.RETRYABLE
    assert outcome.run_id == store.runs[0].id
    assert store.runs[0].failure_code == "artifact_unavailable"
    assert extractor.requests == []


async def test_storage_unavailability_is_distinct_from_provider_failure() -> None:
    store = _Store()
    store.storage_error = FileNotFoundError("object not found")
    extractor = _Extractor()

    outcome = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.RETRYABLE
    assert store.runs[0].failure_code == "artifact_unavailable"
    assert extractor.requests == []


async def test_running_run_replays_by_resuming_the_existing_identity() -> None:
    store = _Store()
    initial = await _service(
        store, _Extractor(error=RuntimeError())
    ).process_submitted_profile_evidence(
        "turn_rear",
    )
    assert initial.status is ProfileInferenceStatus.RETRYABLE
    store.runs[0].status = ProfileInferenceStatus.RUNNING
    store.runs[0].started_at = datetime.now(UTC) - timedelta(seconds=61)

    extractor = _Extractor()
    resumed = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert resumed.status is ProfileInferenceStatus.COMPLETED
    assert len(store.runs) == 1
    assert len(store.claims) == 2
    assert len(extractor.requests) == 1


async def test_active_running_run_is_not_restarted_by_replay() -> None:
    store = _Store()
    initial = await _service(
        store, _Extractor(error=RuntimeError())
    ).process_submitted_profile_evidence(
        "turn_rear",
    )
    assert initial.status is ProfileInferenceStatus.RETRYABLE
    store.runs[0].status = ProfileInferenceStatus.RUNNING
    store.runs[0].started_at = datetime.now(UTC)
    extractor = _Extractor()

    replay = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert replay.status is ProfileInferenceStatus.RUNNING
    assert len(store.runs) == 1
    assert store.claims == []
    assert extractor.requests == []


async def test_all_submitted_ready_images_are_sent_in_one_extractor_request() -> None:
    store = _Store()
    now = datetime(2026, 7, 11, tzinfo=UTC)
    store.artifacts["art_rear_second"] = ArtifactRef(
        id="art_rear_second",
        user_id="usr_rear",
        repair_session_id="rs_rear",
        purpose="diagnostic_photo",
        media_type="image",
        mime_type="image/jpeg",
        filename="rear-second.jpg",
        byte_size=3,
        status="ready",
        content_sha256="b" * 64,
        storage_provider="fake",
        storage_path="rear-second.jpg",
        created_at=now,
        updated_at=now,
    )
    store.turn.message["artifact_ids"] = ["art_rear", "art_rear_second"]
    extractor = _Extractor()

    outcome = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert [image.artifact_id for image in extractor.requests[0].images] == [
        "art_rear",
        "art_rear_second",
    ]


async def test_retryable_failure_replay_does_not_duplicate_evidence() -> None:
    store = _Store()
    failing = _Extractor(error=RuntimeError("provider unavailable"))
    service = _service(store, failing)

    failed = await service.process_submitted_profile_evidence("turn_rear")

    assert failed.status is ProfileInferenceStatus.RETRYABLE
    assert store.runs[0].failure_code == "extractor_failure"
    successful = _Extractor()
    retry = _service(store, successful)
    completed = await retry.process_submitted_profile_evidence("turn_rear")
    replay = await retry.process_submitted_profile_evidence("turn_rear")

    assert completed.status is ProfileInferenceStatus.COMPLETED
    assert replay == completed
    assert len(store.runs) == 1
    assert len(store.claims) == 2
    assert len(successful.requests) == 1


async def test_provider_retry_is_bounded_and_records_privacy_safe_lifecycle() -> None:
    store = _Store()
    telemetry = RecordingProfileInferenceTelemetry()
    extractor = _SequenceExtractor(
        [RuntimeError("provider unavailable"), _valid_single_claim_output()]
    )
    service = ProfileInferenceService(
        turns=store,
        repair_sessions=store,
        bikes=store,
        artifacts=store,
        runs=store,
        storage=store,
        extractor=extractor,
        extractor_version="rear-brake-shadow.v1",
        max_attempts=2,
        resolver_policy=ProfileResolverPolicy.bootstrap_v1(),
        telemetry=telemetry,
    )

    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert store.runs[0].lifecycle_outcomes == [
        "started",
        "retryable_failure",
        "retried",
        "completed",
    ]
    assert len(extractor.requests) == 2
    assert store.bike.technical_profile["brakes"]["rear"]["mechanism"] == "disc"
    event_names = [
        record.name for record in telemetry.records if record.kind == "event"
    ]
    assert "profile_inference_run_retried" in event_names
    assert "profile_inference_run_completed" in event_names
    serialized = repr(telemetry.records)
    assert "provider unavailable" not in serialized
    assert "rear.jpg" not in serialized
    assert "A rotor" not in serialized


async def test_provider_exhaustion_preserves_profile_and_is_operational_only() -> None:
    store = _Store()
    telemetry = RecordingProfileInferenceTelemetry()
    extractor = _SequenceExtractor(
        [RuntimeError("one"), RuntimeError("two"), RuntimeError("three")],
    )
    service = ProfileInferenceService(
        turns=store,
        repair_sessions=store,
        bikes=store,
        artifacts=store,
        runs=store,
        storage=store,
        extractor=extractor,
        extractor_version="rear-brake-shadow.v1",
        max_attempts=3,
        telemetry=telemetry,
    )

    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.EXHAUSTED
    assert store.runs[0].status == "exhausted"
    assert store.runs[0].attempt_count == 3
    assert store.runs[0].retry_count == 2
    assert store.claims == []
    assert store.bike.profile_revision == 0
    assert any(
        record.name == "profile_inference_run_exhausted" for record in telemetry.records
    )


def _service(
    store: _Store,
    extractor: _Extractor,
    *,
    policy: ProfileResolverPolicy | None = None,
) -> ProfileInferenceService:
    return ProfileInferenceService(
        turns=store,
        repair_sessions=store,
        bikes=store,
        artifacts=store,
        runs=store,
        storage=store,
        extractor=extractor,
        extractor_version="rear-brake-shadow.v1",
        resolver_policy=policy,
    )


def _valid_single_claim_output() -> dict[str, object]:
    return deepcopy(
        {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.99,
            },
            "claims": [
                {
                    "field_path": "brakes.rear.mechanism",
                    "value": "disc",
                    "subject_relation": "installed_on_target_bike",
                    "evidence_basis": "direct_visual",
                    "visibility": "clear",
                    "confidence_score": 0.99,
                    "artifact_ids": ["art_rear"],
                    "observed_text": None,
                    "evidence_cues": ["A rotor and rear caliper are visible."],
                },
            ],
            "abstentions": [],
        },
    )


def _brake_claim(
    field_path: str,
    value: object,
    evidence_basis: str,
) -> dict[str, object]:
    return {
        "field_path": field_path,
        "value": value,
        "subject_relation": "installed_on_target_bike",
        "evidence_basis": evidence_basis,
        "visibility": "clear",
        "confidence_score": 0.99,
        "artifact_ids": ["art_rear"],
        "observed_text": "160" if evidence_basis == "readable_marking" else None,
        "evidence_cues": ["Installed rear brake detail is clearly visible."],
    }


def _drivetrain_claim(field_path: str, value: object) -> dict[str, object]:
    return {
        "field_path": field_path,
        "value": value,
        "subject_relation": "installed_on_target_bike",
        "evidence_basis": "direct_visual",
        "visibility": "clear",
        "confidence_score": 0.99,
        "artifact_ids": ["art_rear"],
        "observed_text": None,
        "evidence_cues": ["The drivetrain component is visibly installed."],
    }


def _drivetrain_configuration_claim(
    field_path: str,
    value: object,
) -> dict[str, object]:
    return {
        **_drivetrain_claim(field_path, value),
        "evidence_cues": [
            "The installed drivetrain configuration is directly visible."
        ],
    }


def _drivetrain_identity_claim(field_path: str, value: str) -> dict[str, object]:
    return {
        **_drivetrain_claim(field_path, value),
        "evidence_basis": "readable_marking",
        "observed_text": value,
        "evidence_cues": ["The installed component marking is readable."],
    }


def _rolling_claim(field_path: str, value: object) -> dict[str, object]:
    return {
        "field_path": field_path,
        "value": value,
        "subject_relation": "installed_on_target_bike",
        "evidence_basis": "readable_marking",
        "visibility": "clear",
        "confidence_score": 0.99,
        "artifact_ids": ["art_rear"],
        "observed_text": str(value),
        "evidence_cues": ["Readable marking is visible on the installed rear tire."],
    }


def _direct_rolling_claim(field_path: str, value: object) -> dict[str, object]:
    return {
        **_rolling_claim(field_path, value),
        "evidence_basis": "direct_visual",
        "observed_text": None,
        "evidence_cues": ["The installed rear hub interface is clearly visible."],
    }
