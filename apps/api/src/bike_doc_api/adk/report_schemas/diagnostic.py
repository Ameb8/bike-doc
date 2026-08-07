"""Diagnostic report schema boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from bike_doc_api.schemas.common import APIBaseModel, UserSkillLevel
from bike_doc_api.schemas.report import (
    AlternateHypothesis,
    Diagnosis,
    RepairEstimate,
    SafetyFlag,
)


class DiagnosticReportToolPayload(APIBaseModel):
    """Internal diagnostic report payload accepted from the diagnostic agent."""

    schema_version: Literal["diagnostic_report.v1"]
    primary_diagnosis: Diagnosis
    alternate_hypotheses: list[AlternateHypothesis] = Field(default_factory=list)
    evidence_summary: str
    repair_estimate: RepairEstimate
    key_artifact_ids: list[str]
    user_skill_level: UserSkillLevel
    safety_flags: list[SafetyFlag]


class ObservedFindingToolPayload(APIBaseModel):
    """Agent-facing V2 observation, kept separate from the public schema."""

    finding_id: str = Field(min_length=1)
    component: str
    finding: str = Field(min_length=1)
    evidence_source: Literal[
        "image",
        "user_report",
        "measurement",
        "functional_check",
        "repair_history",
        "other",
    ]
    evidence_source_detail: str | None = None
    relationship_to_symptoms: Literal[
        "unknown",
        "possible_contributor",
        "supports_primary_diagnosis",
        "supported_contributor",
        "incidental",
    ]
    artifact_ids: list[str]


class DiagnosisV2ToolPayload(APIBaseModel):
    """Agent-facing causal diagnosis proposal for V2."""

    component: str
    issue: str
    confidence: Literal["low", "medium", "high"]
    diy_suitability: Literal[
        "unknown", "reasonable", "caution", "shop_recommended", "blocked"
    ]
    supporting_finding_ids: list[str] = Field(min_length=1)


class ContributingFactorToolPayload(APIBaseModel):
    """Agent-facing simultaneous contributor proposal for V2."""

    component: str
    issue: str
    confidence: Literal["low", "medium", "high"]
    evidence_summary: str = Field(min_length=1)
    supporting_finding_ids: list[str] = Field(min_length=1)


class AlternateHypothesisV2ToolPayload(APIBaseModel):
    """Agent-facing alternate proposal for V2."""

    component: str
    issue: str
    confidence: Literal["low", "medium", "high"]
    evidence_summary: str = Field(min_length=1)
    supporting_finding_ids: list[str] = Field(min_length=1)


class DiagnosticReportV2ToolPayload(APIBaseModel):
    """Internal V2 payload; server-owned outcome and archive ID are omitted."""

    schema_version: Literal["diagnostic_report.v2"]
    reported_symptoms: list[str] = Field(min_length=1)
    primary_diagnosis: DiagnosisV2ToolPayload | None
    contributing_factors: list[ContributingFactorToolPayload]
    observed_findings: list[ObservedFindingToolPayload] = Field(min_length=1)
    alternate_hypotheses: list[AlternateHypothesisV2ToolPayload]
    unresolved_uncertainties: list[str]
    evidence_summary: str
    key_artifact_ids: list[str]
    user_skill_level: UserSkillLevel
    safety_flags: list[SafetyFlag]
