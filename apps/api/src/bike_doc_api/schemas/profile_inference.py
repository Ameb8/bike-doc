"""Internal contracts for isolated bike-profile image extraction."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bike_doc_api.services.profile_registry import CANONICAL_FIELD_REGISTRY

INFERENCE_SCHEMA_VERSION = "bike_profile_inference.v1"
BRAKE_INFERENCE_FIELD_PATHS = frozenset(
    path
    for path, field in CANONICAL_FIELD_REGISTRY.items()
    if path.startswith(("brakes.front.", "brakes.rear."))
    and field.volatility_class != "derived"
)
ROLLING_SYSTEM_INFERENCE_FIELD_PATHS = frozenset(
    path
    for path, field in CANONICAL_FIELD_REGISTRY.items()
    if path.startswith(("rolling_system.front.", "rolling_system.rear."))
    and field.volatility_class != "derived"
)
DRIVETRAIN_INFERENCE_FIELD_PATHS = frozenset(
    {
        "drivetrain.architecture",
        "drivetrain.drive_medium",
        *(
            f"drivetrain.{component}.presence"
            for component in (
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
        ),
    },
)
PROFILE_INFERENCE_FIELD_PATHS = (
    BRAKE_INFERENCE_FIELD_PATHS
    | ROLLING_SYSTEM_INFERENCE_FIELD_PATHS
    | DRIVETRAIN_INFERENCE_FIELD_PATHS
)


class ProfileInferenceScene(BaseModel):
    """Scene-level context returned by the isolated extractor."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contains_bicycle: bool
    multiple_bicycles: bool
    target_relation: Literal[
        "installed_on_target_bike",
        "likely_installed_on_target_bike",
        "loose_component",
        "packaging_or_reference",
        "other_bike",
        "ambiguous",
    ]
    confidence_score: float = Field(ge=0, le=1, allow_inf_nan=False)


class ProfileInferenceClaim(BaseModel):
    """One structured candidate claim before server-side registry validation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    field_path: str = Field(min_length=1)
    value: Any
    subject_relation: Literal[
        "installed_on_target_bike",
        "likely_installed_on_target_bike",
        "loose_component",
        "packaging_or_reference",
        "other_bike",
        "ambiguous",
    ]
    evidence_basis: Literal[
        "readable_marking", "direct_visual", "counted_visual", "derived_visual"
    ]
    visibility: Literal["clear", "partial", "poor"]
    confidence_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    artifact_ids: list[str] = Field(min_length=1)
    observed_text: str | None = None
    evidence_cues: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("artifact_ids")
    @classmethod
    def validate_artifact_ids(cls, value: list[str]) -> list[str]:
        """Reject blank or repeated evidence references."""

        normalized = [artifact_id.strip() for artifact_id in value]
        if any(not artifact_id for artifact_id in normalized):
            raise ValueError("artifact_ids must not contain blank IDs")
        if len(normalized) != len(set(normalized)):
            raise ValueError("artifact_ids must not contain duplicates")
        return normalized

    @field_validator("evidence_cues")
    @classmethod
    def validate_evidence_cues(cls, value: list[str]) -> list[str]:
        """Keep audit cues short, factual strings rather than long explanations."""

        normalized = [cue.strip() for cue in value]
        if any(not cue for cue in normalized):
            raise ValueError("evidence_cues must not contain blank values")
        return normalized


class ProfileInferenceAbstention(BaseModel):
    """An explicit extractor abstention for one tracer field."""

    model_config = ConfigDict(extra="forbid", strict=True)

    field_path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ProfileInferenceOutput(BaseModel):
    """Strict `bike_profile_inference.v1` output contract."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["bike_profile_inference.v1"]
    scene: ProfileInferenceScene
    claims: list[ProfileInferenceClaim] = Field(default_factory=list)
    abstentions: list[ProfileInferenceAbstention] = Field(default_factory=list)


class InferenceImage(BaseModel):
    """Private image bytes supplied to a provider adapter."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    content: bytes = Field(min_length=1)


class ProfileInferenceRequest(BaseModel):
    """Minimal, server-owned input for one isolated extraction call."""

    model_config = ConfigDict(extra="forbid")

    bike_id: str = Field(min_length=1)
    repair_session_id: str = Field(min_length=1)
    caption: str | None = None
    images: list[InferenceImage] = Field(min_length=1)
    schema_version: Literal["bike_profile_inference.v1"] = "bike_profile_inference.v1"
    allowed_field_paths: list[str] = Field(
        default_factory=lambda: sorted(PROFILE_INFERENCE_FIELD_PATHS),
    )
