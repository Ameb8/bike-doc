"""Report API schemas and mappers."""

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from bike_doc_api.models.phase_report import PhaseReport as PhaseReportModel
from bike_doc_api.schemas.common import (
    APIBaseModel,
    Confidence,
    PhaseReportType,
    RepairSessionPhase,
    SafetySeverity,
    UserSkillLevel,
)


class SafetyFlag(APIBaseModel):
    """Public safety flag."""

    code: str
    severity: SafetySeverity
    phase: RepairSessionPhase
    message: str
    blocks_repair_instructions: bool

    @model_validator(mode="after")
    def require_blocking_flags_to_block_instructions(self) -> Self:
        """Blocking flags must also block repair instructions."""

        if (
            self.severity is SafetySeverity.BLOCKING
            and not self.blocks_repair_instructions
        ):
            msg = "blocking safety flags must block repair instructions"
            raise ValueError(msg)
        return self


class Diagnosis(APIBaseModel):
    """Primary diagnostic conclusion."""

    component: str
    issue: str
    confidence: Confidence
    diy_suitability: (
        Literal["unknown", "reasonable", "caution", "shop_recommended", "blocked"]
        | None
    ) = "unknown"


class AlternateHypothesis(APIBaseModel):
    """Alternate diagnostic hypothesis."""

    component: str
    issue: str
    confidence: Confidence
    ruled_out_by: str | None = None


class DiagnosticReportV1(APIBaseModel):
    """Diagnostic report payload."""

    schema_version: Literal["diagnostic_report.v1"]
    primary_diagnosis: Diagnosis
    alternate_hypotheses: list[AlternateHypothesis]
    evidence_summary: str
    key_artifact_ids: list[str]
    user_skill_level: UserSkillLevel
    safety_flags: list[SafetyFlag]
    diagnostic_session_id: str


class PhaseReportEnvelope(APIBaseModel):
    """Public phase report envelope."""

    id: str
    repair_session_id: str
    type: PhaseReportType
    schema_version: str
    phase: RepairSessionPhase
    summary: str
    safety_flags: list[SafetyFlag]
    source_artifact_ids: list[str]
    created_at: datetime
    payload: DiagnosticReportV1 | dict[str, Any] = Field(union_mode="left_to_right")

    @model_validator(mode="after")
    def validate_diagnostic_payload(self) -> Self:
        """Validate diagnostic report envelopes against the diagnostic payload."""

        if (
            self.type is PhaseReportType.DIAGNOSTIC
            or self.schema_version == "diagnostic_report.v1"
        ):
            if self.schema_version != "diagnostic_report.v1":
                msg = "diagnostic reports must use diagnostic_report.v1"
                raise ValueError(msg)
            if self.phase is not RepairSessionPhase.DIAGNOSTIC:
                msg = "diagnostic reports must use diagnostic phase"
                raise ValueError(msg)
            if not isinstance(self.payload, DiagnosticReportV1):
                self.payload = DiagnosticReportV1.model_validate(self.payload)
            if self.payload.safety_flags != self.safety_flags:
                msg = "diagnostic report safety flags must match envelope"
                raise ValueError(msg)
        return self


def phase_report_envelope_from_model(report: PhaseReportModel) -> PhaseReportEnvelope:
    """Map a persistence phase report to the public schema."""

    return PhaseReportEnvelope(
        id=report.id,
        repair_session_id=report.repair_session_id,
        type=PhaseReportType(report.type),
        schema_version=report.schema_version,
        phase=RepairSessionPhase(report.phase),
        summary=report.summary,
        safety_flags=[SafetyFlag.model_validate(flag) for flag in report.safety_flags],
        source_artifact_ids=report.source_artifact_ids,
        created_at=report.created_at,
        payload=report.payload,
    )
