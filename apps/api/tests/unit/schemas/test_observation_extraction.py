"""Strict contracts for diagnostic visual-observation extraction."""

from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from bike_doc_api.schemas.observation_extraction import (
    ArtifactProcessingStatus,
    DiagnosticVisualObservationProjection,
    NormalizedModelImage,
    ObservationExtractionOutput,
    validate_observation_output,
)


def make_image(artifact_id: str = "art_rear") -> NormalizedModelImage:
    """Build one normalized image owned by the extraction boundary."""

    return NormalizedModelImage(
        artifact_id=artifact_id,
        mime_type="image/jpeg",
        content=b"normalized-image",
        original_width=1000,
        original_height=750,
        normalized_width=1000,
        normalized_height=750,
        content_sha256="a" * 64,
        preprocessing_version="diagnostic-image-jpeg-v1",
    )


def make_output() -> dict[str, object]:
    """Return a valid structured extractor response."""

    return {
        "schema_version": "visual-observation.v1",
        "image_assessments": [
            {
                "artifact_id": "art_rear",
                "assessability": "usable",
                "visible_areas": ["rear brake"],
                "limitations": [],
            },
        ],
        "observations": [
            {
                "artifact_ids": ["art_rear"],
                "component_or_area": "rear brake caliper",
                "position": "rear",
                "finding": "Dark residue is visible below the bleed port.",
                "evidence_cues": ["A dark wet-looking band is below the port."],
                "visibility": "clear",
                "raw_model_score": 0.8,
                "safety_relevant": True,
            },
        ],
        "suggested_follow_up": {
            "kind": "photo",
            "request": "Provide a straight-on caliper photo.",
        },
    }


def test_validates_and_projects_an_artifact_backed_observation() -> None:
    output = ObservationExtractionOutput.model_validate(make_output())

    validated = validate_observation_output(output, [make_image()])

    assert validated.diagnostic_agent_projection().model_dump() == {
        "image_assessments": [
            {
                "artifact_id": "art_rear",
                "assessability": "usable",
                "visible_areas": ["rear brake"],
                "limitations": [],
            },
        ],
        "observations": [
            {
                "artifact_ids": ["art_rear"],
                "component_or_area": "rear brake caliper",
                "position": "rear",
                "finding": "Dark residue is visible below the bleed port.",
                "evidence_cues": ["A dark wet-looking band is below the port."],
                "visibility": "clear",
                "safety_relevant": True,
            },
        ],
        "suggested_follow_up": {
            "kind": "photo",
            "request": "Provide a straight-on caliper photo.",
        },
    }


def test_valid_empty_observation_output_serializes_without_raw_scores() -> None:
    output = ObservationExtractionOutput.model_validate(
        {
            "schema_version": "visual-observation.v1",
            "image_assessments": [
                {
                    "artifact_id": "art_rear",
                    "assessability": "unusable",
                    "visible_areas": [],
                    "limitations": [
                        {"type": "darkness", "description": "The bike is not visible."},
                    ],
                },
            ],
            "observations": [],
            "suggested_follow_up": None,
        },
    )

    validated = validate_observation_output(output, [make_image()])
    serialized = validated.diagnostic_agent_projection().model_dump_json()

    assert json.loads(serialized) == {
        "image_assessments": [
            {
                "artifact_id": "art_rear",
                "assessability": "unusable",
                "visible_areas": [],
                "limitations": [
                    {
                        "type": "darkness",
                        "description": "The bike is not visible.",
                    },
                ],
            },
        ],
        "observations": [],
        "suggested_follow_up": None,
    }
    assert "raw_model_score" not in serialized


def test_diagnostic_projection_rejects_raw_model_scores_structurally() -> None:
    with pytest.raises(ValidationError):
        DiagnosticVisualObservationProjection.model_validate(
            {
                "observations": [
                    {
                        "artifact_ids": ["art_rear"],
                        "component_or_area": "rear brake caliper",
                        "position": "rear",
                        "finding": "Dark residue is visible below the bleed port.",
                        "evidence_cues": ["A dark wet-looking band is below the port."],
                        "visibility": "clear",
                        "safety_relevant": True,
                        "raw_model_score": 0.8,
                    },
                ],
            },
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "artifact_id": "art_rear",
            "status": "available",
            "failure_code": "image_decode_failed",
        },
        {"artifact_id": "art_rear", "status": "unavailable"},
        {
            "artifact_id": "art_rear",
            "status": "unavailable",
            "failure_code": "provider_timeout",
        },
    ],
)
def test_rejects_invalid_artifact_processing_statuses(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ArtifactProcessingStatus.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "images"),
    [
        (lambda data: data["image_assessments"].clear(), [make_image()]),
        (
            lambda data: data["image_assessments"].append(
                data["image_assessments"][0].copy(),
            ),
            [make_image()],
        ),
        (
            lambda data: data["image_assessments"].__setitem__(
                0,
                {
                    **data["image_assessments"][0],
                    "artifact_id": "art_unknown",
                },
            ),
            [make_image()],
        ),
    ],
    ids=["missing", "duplicate", "unknown"],
)
def test_rejects_invalid_image_assessment_coverage(
    mutation: object, images: list[NormalizedModelImage]
) -> None:
    data = make_output()
    mutation(data)  # type: ignore[operator]
    output = ObservationExtractionOutput.model_validate(data)

    with pytest.raises(ValueError):
        validate_observation_output(output, images)


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("image_assessments", 0, "assessability"), "maybe"),
        (("image_assessments", 0, "limitations", 0, "type"), "shadow"),
        (("observations", 0, "position"), "left"),
        (("observations", 0, "visibility"), "mostly_clear"),
    ],
)
def test_rejects_unsupported_enum_values(
    path: tuple[str | int, ...], invalid_value: str
) -> None:
    data = make_output()
    if path[0] == "image_assessments" and path[2] == "limitations":
        data["image_assessments"][0]["limitations"] = [  # type: ignore[index]
            {"type": "blur", "description": "The edge is soft."},
        ]
    target: object = data
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = invalid_value  # type: ignore[index]

    with pytest.raises(ValidationError):
        ObservationExtractionOutput.model_validate(data)


@pytest.mark.parametrize("score", [-0.01, 1.01, math.inf, -math.inf, math.nan])
def test_rejects_nonfinite_or_out_of_range_raw_model_scores(score: float) -> None:
    data = make_output()
    data["observations"][0]["raw_model_score"] = score  # type: ignore[index]

    with pytest.raises(ValidationError):
        ObservationExtractionOutput.model_validate(data)


@pytest.mark.parametrize("artifact_ids", [[], [""], ["art_rear", "art_rear"]])
def test_rejects_blank_or_duplicate_observation_artifact_references(
    artifact_ids: list[str],
) -> None:
    data = make_output()
    data["observations"][0]["artifact_ids"] = artifact_ids  # type: ignore[index]

    with pytest.raises(ValidationError):
        ObservationExtractionOutput.model_validate(data)


def test_rejects_observation_referencing_an_unknown_input_artifact() -> None:
    data = make_output()
    data["observations"][0]["artifact_ids"] = ["art_unknown"]  # type: ignore[index]
    output = ObservationExtractionOutput.model_validate(data)

    with pytest.raises(ValueError):
        validate_observation_output(output, [make_image()])


def test_rejects_more_than_twelve_observations() -> None:
    data = make_output()
    data["observations"] = [data["observations"][0].copy() for _ in range(13)]  # type: ignore[index]

    with pytest.raises(ValidationError):
        ObservationExtractionOutput.model_validate(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.__setitem__("schema_version", "visual-observation.v2"),
        lambda data: data.__setitem__("unexpected", "value"),
        lambda data: data["observations"][0].__setitem__("unexpected", "value"),
        lambda data: data["image_assessments"][0].__setitem__("unexpected", "value"),
        lambda data: data["suggested_follow_up"].__setitem__("unexpected", "value"),
        lambda data: data.__setitem__("abstentions", []),
    ],
    ids=[
        "unknown-schema-version",
        "unknown-output-field",
        "unknown-observation-field",
        "unknown-assessment-field",
        "unknown-follow-up-field",
        "unsupported-abstentions-field",
    ],
)
def test_rejects_unknown_schema_versions_and_fields(mutation: object) -> None:
    data = make_output()
    mutation(data)  # type: ignore[operator]

    with pytest.raises(ValidationError):
        ObservationExtractionOutput.model_validate(data)
