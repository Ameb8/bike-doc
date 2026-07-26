"""App-owned contracts for isolated diagnostic visual-observation extraction."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OBSERVATION_EXTRACTION_SCHEMA_VERSION = "visual-observation.v1"


class _StrictInternalModel(BaseModel):
    """Reject coercion and unknown fields at an internal trust boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class NormalizedModelImage(_StrictInternalModel):
    """Ephemeral normalized image bytes supplied to a model view."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_id: str = Field(min_length=1)
    mime_type: Literal["image/jpeg"]
    content: bytes = Field(min_length=1)
    original_width: int = Field(gt=0)
    original_height: int = Field(gt=0)
    normalized_width: int = Field(gt=0)
    normalized_height: int = Field(gt=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    preprocessing_version: str = Field(min_length=1)

    @field_validator("artifact_id", "preprocessing_version")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        """Keep server-owned input labels explicit and usable."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class ArtifactProcessingStatus(_StrictInternalModel):
    """Typed status for one submitted artifact excluded before model access."""

    artifact_id: str = Field(min_length=1)
    status: Literal["available", "unavailable"]
    failure_code: (
        Literal[
            "image_not_ready",
            "image_decode_failed",
            "image_normalization_failed",
            "image_analysis_unavailable",
        ]
        | None
    ) = None
    retryable: bool = False

    @field_validator("artifact_id")
    @classmethod
    def reject_blank_artifact_id(cls, value: str) -> str:
        """Ensure status entries remain keyed to one concrete artifact."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("artifact_id must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_failure_shape(self) -> ArtifactProcessingStatus:
        """Keep unavailable statuses explicit without inventing provider detail."""

        if self.status == "available" and self.failure_code is not None:
            raise ValueError("available artifacts must not have a failure_code")
        if self.status == "unavailable" and self.failure_code is None:
            raise ValueError("unavailable artifacts require a failure_code")
        return self


class ImageLimitation(_StrictInternalModel):
    """One factual reason an image cannot support a reliable assessment."""

    type: Literal[
        "blur",
        "glare",
        "darkness",
        "framing",
        "distance",
        "occlusion",
        "perspective",
        "other",
    ]
    description: str = Field(min_length=1)

    @field_validator("description")
    @classmethod
    def reject_blank_description(cls, value: str) -> str:
        """Require a short factual limitation rather than an empty label."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("description must not be blank")
        return normalized


class ImageAssessment(_StrictInternalModel):
    """Assessability and quality limits for one normalized extractor input."""

    artifact_id: str = Field(min_length=1)
    assessability: Literal["usable", "limited", "unusable"]
    visible_areas: list[str] = Field(default_factory=list)
    limitations: list[ImageLimitation] = Field(default_factory=list)

    @field_validator("artifact_id")
    @classmethod
    def reject_blank_artifact_id(cls, value: str) -> str:
        """Keep assessment coverage keyed to an input artifact."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("artifact_id must not be blank")
        return normalized

    @field_validator("visible_areas")
    @classmethod
    def reject_blank_visible_areas(cls, value: list[str]) -> list[str]:
        """Do not permit empty pseudo-areas in an assessment."""

        normalized = [area.strip() for area in value]
        if any(not area for area in normalized):
            raise ValueError("visible_areas must not contain blank values")
        return normalized


class VisualObservation(_StrictInternalModel):
    """One artifact-backed, context-free visual condition cue."""

    artifact_ids: list[str] = Field(min_length=1)
    component_or_area: str | None = None
    position: Literal["front", "rear", "whole-bike", "unknown"]
    finding: str = Field(min_length=1)
    evidence_cues: list[str] = Field(min_length=1)
    visibility: Literal["clear", "partial", "poor"]
    raw_model_score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    safety_relevant: bool

    @field_validator("artifact_ids")
    @classmethod
    def validate_artifact_ids(cls, value: list[str]) -> list[str]:
        """Reject blank or duplicate evidence references."""

        normalized = [artifact_id.strip() for artifact_id in value]
        if any(not artifact_id for artifact_id in normalized):
            raise ValueError("artifact_ids must not contain blank IDs")
        if len(normalized) != len(set(normalized)):
            raise ValueError("artifact_ids must not contain duplicates")
        return normalized

    @field_validator("component_or_area", "finding")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Reject empty findings while retaining an unidentified area as null."""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("text values must not be blank")
        return normalized

    @field_validator("evidence_cues")
    @classmethod
    def validate_evidence_cues(cls, value: list[str]) -> list[str]:
        """Require concise, non-empty visible evidence cues."""

        normalized = [cue.strip() for cue in value]
        if any(not cue for cue in normalized):
            raise ValueError("evidence_cues must not contain blank values")
        return normalized


class SuggestedFollowUp(_StrictInternalModel):
    """One optional targeted request the diagnostic agent may consider."""

    kind: Literal["photo", "measurement", "text"]
    request: str = Field(min_length=1)

    @field_validator("request")
    @classmethod
    def reject_blank_request(cls, value: str) -> str:
        """Keep follow-up suggestions specific enough to be useful."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("request must not be blank")
        return normalized


class DiagnosticVisualObservation(_StrictInternalModel):
    """The sole score-free observation shape exposed to diagnostic context."""

    artifact_ids: list[str]
    component_or_area: str | None
    position: Literal["front", "rear", "whole-bike", "unknown"]
    finding: str
    evidence_cues: list[str]
    visibility: Literal["clear", "partial", "poor"]
    safety_relevant: bool


class DiagnosticVisualObservationProjection(_StrictInternalModel):
    """Score-free visual evidence supplied to the diagnostic agent."""

    image_assessments: list[ImageAssessment] = Field(default_factory=list)
    observations: list[DiagnosticVisualObservation]
    suggested_follow_up: SuggestedFollowUp | None = None


class ObservationExtractionOutput(_StrictInternalModel):
    """Strict `visual-observation.v1` response from the isolated extractor."""

    schema_version: Literal["visual-observation.v1"]
    image_assessments: list[ImageAssessment]
    observations: list[VisualObservation] = Field(default_factory=list, max_length=12)
    suggested_follow_up: SuggestedFollowUp | None = None

    def diagnostic_agent_projection(self) -> DiagnosticVisualObservationProjection:
        """Project validated observations without exposing extractor score data."""

        return DiagnosticVisualObservationProjection(
            image_assessments=self.image_assessments,
            observations=[
                DiagnosticVisualObservation(
                    artifact_ids=observation.artifact_ids,
                    component_or_area=observation.component_or_area,
                    position=observation.position,
                    finding=observation.finding,
                    evidence_cues=observation.evidence_cues,
                    visibility=observation.visibility,
                    safety_relevant=observation.safety_relevant,
                )
                for observation in self.observations
            ],
            suggested_follow_up=self.suggested_follow_up,
        )


def validate_observation_output(
    output: ObservationExtractionOutput,
    normalized_images: Sequence[NormalizedModelImage],
) -> ObservationExtractionOutput:
    """Validate extractor coverage and evidence references against its inputs."""

    input_artifact_ids = [image.artifact_id for image in normalized_images]
    if not input_artifact_ids:
        raise ValueError(
            "observation extraction requires at least one normalized image"
        )
    if len(input_artifact_ids) != len(set(input_artifact_ids)):
        raise ValueError("normalized input artifact IDs must be unique")

    assessment_artifact_ids = [
        assessment.artifact_id for assessment in output.image_assessments
    ]
    if len(assessment_artifact_ids) != len(set(assessment_artifact_ids)):
        raise ValueError("image assessments must not contain duplicate artifact IDs")
    if set(assessment_artifact_ids) != set(input_artifact_ids):
        raise ValueError(
            "image assessments must contain exactly the normalized input IDs"
        )

    known_artifact_ids = set(input_artifact_ids)
    for observation in output.observations:
        unknown_artifact_ids = set(observation.artifact_ids) - known_artifact_ids
        if unknown_artifact_ids:
            raise ValueError("observations must cite only normalized input artifacts")

    return output
