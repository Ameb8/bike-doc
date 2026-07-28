"""Service seam for isolated diagnostic visual-observation extraction."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bike_doc_api.schemas.observation_extraction import (
    OBSERVATION_EXTRACTION_SCHEMA_VERSION,
    NormalizedModelImage,
    ObservationExtractionOutput,
)

_MAX_USAGE_VALUE = 2_147_483_647
_MAX_COST_USD = 1_000_000.0


class ObservationExtractionRequest(BaseModel):
    """The complete, server-owned input permitted at the provider boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    images: list[NormalizedModelImage] = Field(min_length=1, max_length=3)
    schema_version: str = OBSERVATION_EXTRACTION_SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def require_supported_schema_version(cls, value: str) -> str:
        """Keep the provider request tied to the app-owned output contract."""

        if value != OBSERVATION_EXTRACTION_SCHEMA_VERSION:
            raise ValueError("unsupported observation extraction schema version")
        return value

    @model_validator(mode="after")
    def require_unique_artifact_ids(self) -> ObservationExtractionRequest:
        """Avoid sending the same server-owned normalized image twice."""

        artifact_ids = [image.artifact_id for image in self.images]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("observation extraction image artifact IDs must be unique")
        return self


class ObservationExtractionUsage(BaseModel):
    """Bounded numeric provider metadata safe for lifecycle telemetry only."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    input_tokens: int | None = Field(default=None, ge=0, le=_MAX_USAGE_VALUE)
    output_tokens: int | None = Field(default=None, ge=0, le=_MAX_USAGE_VALUE)
    total_tokens: int | None = Field(default=None, ge=0, le=_MAX_USAGE_VALUE)
    cost_usd: float | None = Field(
        default=None,
        ge=0.0,
        le=_MAX_COST_USD,
        allow_inf_nan=False,
    )


class ObservationExtractionResult(BaseModel):
    """Validated output and operational metadata returned to the lifecycle service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    output: ObservationExtractionOutput
    usage: ObservationExtractionUsage


class DiagnosticObservationExtractor(Protocol):
    """External adapter used by the visual-evidence lifecycle service."""

    provider: str
    model: str

    async def extract(
        self,
        request: ObservationExtractionRequest,
    ) -> ObservationExtractionResult:
        """Inspect one normalized-image batch in a fresh provider context."""
