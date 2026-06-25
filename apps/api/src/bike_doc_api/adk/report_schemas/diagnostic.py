"""Diagnostic report schema boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from bike_doc_api.schemas.common import APIBaseModel, UserSkillLevel
from bike_doc_api.schemas.report import AlternateHypothesis, Diagnosis, SafetyFlag


class DiagnosticReportToolPayload(APIBaseModel):
    """Internal diagnostic report payload accepted from the diagnostic agent."""

    schema_version: Literal["diagnostic_report.v1"]
    primary_diagnosis: Diagnosis
    alternate_hypotheses: list[AlternateHypothesis] = Field(default_factory=list)
    evidence_summary: str
    key_artifact_ids: list[str]
    user_skill_level: UserSkillLevel
    safety_flags: list[SafetyFlag]
