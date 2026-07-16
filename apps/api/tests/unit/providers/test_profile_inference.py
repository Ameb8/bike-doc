"""Provider contract tests for isolated profile extraction."""

from __future__ import annotations

import json
from types import SimpleNamespace

from bike_doc_api.providers.profile_inference.gemini import (
    GeminiProfileInferenceExtractor,
)
from bike_doc_api.schemas.profile_inference import (
    InferenceImage,
    ProfileInferenceRequest,
)


async def test_extractor_sends_versioned_topology_registry_to_model() -> None:
    captured: dict[str, object] = {}

    async def generate_content(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            parsed={
                "schema_version": "bike_profile_inference.v1",
                "scene": {
                    "contains_bicycle": True,
                    "multiple_bicycles": False,
                    "target_relation": "installed_on_target_bike",
                    "confidence_score": 0.99,
                },
                "claims": [],
                "abstentions": [],
            },
            usage_metadata=None,
        )

    extractor = GeminiProfileInferenceExtractor(
        model="test-model",
        timeout_seconds=1,
        generate_content=generate_content,
    )
    request = ProfileInferenceRequest(
        bike_id="bike_test",
        repair_session_id="rs_test",
        images=[
            InferenceImage(
                artifact_id="art_test",
                mime_type="image/jpeg",
                content=b"image",
            ),
        ],
    )

    await extractor.extract(request)

    metadata = json.loads(captured["contents"][0])  # type: ignore[index]
    fields = metadata["field_registry"]["fields"]
    assert fields["drivetrain.architecture"]["allowed_values"] == [
        "continuously_variable",
        "derailleur",
        "fixed_gear",
        "gearbox",
        "internal_gear_hub",
        "other",
        "singlespeed_freewheel",
    ]
    assert fields["drivetrain.front_derailleur.presence"]["allowed_values"] == [
        "absent",
        "present",
        "unknown",
    ]
    assert "drivetrain.rear_speed_count" not in fields
    assert "drivetrain.legacy_description" not in fields


async def test_extractor_sends_drivetrain_roles_and_identity() -> None:
    captured: dict[str, object] = {}

    async def generate_content(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            parsed={
                "schema_version": "bike_profile_inference.v1",
                "scene": {
                    "contains_bicycle": True,
                    "multiple_bicycles": False,
                    "target_relation": "installed_on_target_bike",
                    "confidence_score": 0.99,
                },
                "claims": [],
                "abstentions": [],
            },
            usage_metadata=None,
        )

    extractor = GeminiProfileInferenceExtractor(
        model="test-model",
        timeout_seconds=1,
        generate_content=generate_content,
    )
    request = ProfileInferenceRequest(
        bike_id="bike_test",
        repair_session_id="rs_test",
        images=[
            InferenceImage(
                artifact_id="art_test",
                mime_type="image/jpeg",
                content=b"image",
            ),
        ],
    )

    await extractor.extract(request)

    metadata = json.loads(captured["contents"][0])  # type: ignore[index]
    fields = metadata["field_registry"]["fields"]
    drivetrain_roles = (
        "front_shifter",
        "rear_shifter",
        "front_derailleur",
        "rear_derailleur",
        "crankset",
        "rear_cluster",
        "chain",
        "belt",
        "gear_unit",
        "bottom_bracket",
    )
    for role in drivetrain_roles:
        assert f"drivetrain.{role}.manufacturer" in fields
        assert f"drivetrain.{role}.model" in fields

    assert fields["drivetrain.front_shifter.actuation"]["permitted_evidence_bases"] == [
        "direct_visual"
    ]
    assert fields["drivetrain.rear_shifter.actuation"]["permitted_evidence_bases"] == [
        "direct_visual"
    ]
    assert fields["drivetrain.rear_derailleur.mount_type"][
        "permitted_evidence_bases"
    ] == ["direct_visual"]
    assert fields["drivetrain.rear_cluster.cluster_type"][
        "permitted_evidence_bases"
    ] == ["direct_visual"]

    assert fields["drivetrain.crankset.chainring_count"][
        "permitted_evidence_bases"
    ] == ["counted_visual", "readable_marking"]
    assert fields["drivetrain.rear_cluster.driver_interface"][
        "permitted_evidence_bases"
    ] == ["readable_marking"]
    assert fields["drivetrain.bottom_bracket.shell_width_mm"][
        "permitted_evidence_bases"
    ] == ["readable_marking"]

    instruction = captured["config"].system_instruction
    assert "You may emit drivetrain count, tooth, speed-compatibility" in instruction
    assert "Do not infer counts, tooth values" not in instruction
    assert "drivetrain.front_chainring_count" in instruction
    assert "drivetrain.rear_speed_count" in instruction


async def test_extractor_instruction_separates_identity_appearance_and_privacy() -> (
    None
):
    captured: dict[str, object] = {}

    async def generate_content(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            parsed={
                "schema_version": "bike_profile_inference.v1",
                "scene": {
                    "contains_bicycle": True,
                    "multiple_bicycles": False,
                    "target_relation": "installed_on_target_bike",
                    "confidence_score": 0.99,
                },
                "claims": [],
                "abstentions": [],
            },
            usage_metadata=None,
        )

    extractor = GeminiProfileInferenceExtractor(
        model="test-model",
        timeout_seconds=1,
        generate_content=generate_content,
    )
    await extractor.extract(
        ProfileInferenceRequest(
            bike_id="bike_test",
            repair_session_id="rs_test",
            images=[
                InferenceImage(
                    artifact_id="art_test", mime_type="image/jpeg", content=b"image"
                )
            ],
        )
    )

    instruction = captured["config"].system_instruction  # type: ignore[index]
    assert "visual look-alike must never be presented as exact identity" in instruction
    assert "frame serial" in instruction
    assert "VINs" in instruction
