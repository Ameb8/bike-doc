"""Report API schemas and mappers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

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


class RepairTimeEstimate(APIBaseModel):
    """Predicted at-home repair duration."""

    low_minutes: int
    high_minutes: int


class ShopRepairCostEstimate(APIBaseModel):
    """Predicted shop repair cost."""

    low_usd: int
    high_usd: int
    notes: str | None = None


class CostEstimateSource(StrEnum):
    """Source used for a public cost estimate."""

    MANUAL_TABLE = "manual_table"
    LABOR_REFERENCE_TABLE = "labor_reference_table"
    SEARCH_PROVIDER = "search_provider"
    CACHED_LOOKUP = "cached_lookup"
    UNAVAILABLE = "unavailable"


class CostEstimate(APIBaseModel):
    """Public price estimate range."""

    currency: str = "USD"
    min_amount: float | None = Field(default=None, ge=0)
    max_amount: float | None = Field(default=None, ge=0)
    confidence: Confidence
    source: CostEstimateSource
    notes: str | None = None

    @model_validator(mode="after")
    def validate_amount_order(self) -> Self:
        """Ensure a range is ordered when both bounds are present."""

        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            msg = "min_amount must be less than or equal to max_amount"
            raise ValueError(msg)
        return self


class RepairEstimate(APIBaseModel):
    """LLM-predicted repair estimate for the diagnostic report."""

    difficulty: Literal["easy", "medium", "hard"]
    difficulty_notes: str
    tools_required: list[str]
    parts_required: list[str]
    repair_time: RepairTimeEstimate
    shop_repair_cost: ShopRepairCostEstimate


class DiagnosticReportV1(APIBaseModel):
    """Diagnostic report payload."""

    schema_version: Literal["diagnostic_report.v1"]
    primary_diagnosis: Diagnosis
    alternate_hypotheses: list[AlternateHypothesis]
    evidence_summary: str
    repair_estimate: RepairEstimate
    key_artifact_ids: list[str]
    user_skill_level: UserSkillLevel
    safety_flags: list[SafetyFlag]
    diagnostic_session_id: str
    cost_estimate: PlanCostEstimate | None = None


class DiagnosticOutcome(StrEnum):
    """Server-owned diagnostic completion outcome."""

    DIAGNOSIS_SUPPORTED = "diagnosis_supported"
    USER_DECLINED_MORE_INPUT = "user_declined_more_input"
    REQUESTED_INPUT_UNAVAILABLE = "requested_input_unavailable"
    IN_PERSON_ASSESSMENT_REQUIRED = "in_person_assessment_required"


class EvidenceSource(StrEnum):
    """Method that produced an observed finding."""

    IMAGE = "image"
    USER_REPORT = "user_report"
    MEASUREMENT = "measurement"
    FUNCTIONAL_CHECK = "functional_check"
    REPAIR_HISTORY = "repair_history"
    OTHER = "other"


class DiagnosticRelevance(StrEnum):
    """Evidence-backed relationship of a finding to the complaint."""

    UNKNOWN = "unknown"
    POSSIBLE_CONTRIBUTOR = "possible_contributor"
    SUPPORTS_PRIMARY_DIAGNOSIS = "supports_primary_diagnosis"
    SUPPORTED_CONTRIBUTOR = "supported_contributor"
    INCIDENTAL = "incidental"


class ObservedFinding(APIBaseModel):
    """A report-local, evidence-backed observation."""

    finding_id: str = Field(min_length=1)
    component: str
    finding: str = Field(min_length=1)
    evidence_source: EvidenceSource
    evidence_source_detail: str | None = None
    relationship_to_symptoms: DiagnosticRelevance
    artifact_ids: list[str]

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> Self:
        """Keep source detail and artifact references aligned with their source."""

        if self.evidence_source is EvidenceSource.OTHER:
            if (
                self.evidence_source_detail is None
                or not self.evidence_source_detail.strip()
            ):
                raise ValueError("other evidence sources require non-blank detail")
        elif self.evidence_source_detail is not None:
            raise ValueError("named evidence sources require null detail")
        if self.evidence_source is EvidenceSource.IMAGE:
            if not self.artifact_ids:
                raise ValueError("image findings require at least one artifact ID")
        elif self.artifact_ids:
            raise ValueError("non-image findings require empty artifact IDs")
        return self


class DiagnosisV2(APIBaseModel):
    """A causal diagnosis with explicit finding references."""

    component: str
    issue: str
    confidence: Literal["low", "medium", "high"]
    diy_suitability: Literal[
        "unknown", "reasonable", "caution", "shop_recommended", "blocked"
    ]
    supporting_finding_ids: list[str] = Field(min_length=1)


class ContributingFactor(APIBaseModel):
    """A supported simultaneous contributor."""

    component: str
    issue: str
    confidence: Literal["low", "medium", "high"]
    evidence_summary: str = Field(min_length=1)
    supporting_finding_ids: list[str] = Field(min_length=1)

    @field_validator("evidence_summary")
    @classmethod
    def require_non_blank_evidence_summary(cls, value: str) -> str:
        """Reject whitespace-only causal evidence summaries."""

        if not value.strip():
            raise ValueError("evidence_summary must be non-blank")
        return value


class AlternateHypothesisV2(APIBaseModel):
    """An evidence-backed causal alternative that remains possible."""

    component: str
    issue: str
    confidence: Literal["low", "medium", "high"]
    evidence_summary: str = Field(min_length=1)
    supporting_finding_ids: list[str] = Field(min_length=1)

    @field_validator("evidence_summary")
    @classmethod
    def require_non_blank_evidence_summary(cls, value: str) -> str:
        """Reject whitespace-only alternate evidence summaries."""

        if not value.strip():
            raise ValueError("evidence_summary must be non-blank")
        return value


class DiagnosticReportV2(APIBaseModel):
    """Version two public diagnostic report payload."""

    schema_version: Literal["diagnostic_report.v2"]
    diagnostic_outcome: DiagnosticOutcome
    reported_symptoms: list[str] = Field(min_length=1)
    primary_diagnosis: DiagnosisV2 | None
    contributing_factors: list[ContributingFactor]
    observed_findings: list[ObservedFinding] = Field(min_length=1)
    alternate_hypotheses: list[AlternateHypothesisV2]
    unresolved_uncertainties: list[str]
    evidence_summary: str
    key_artifact_ids: list[str]
    user_skill_level: UserSkillLevel
    safety_flags: list[SafetyFlag]
    diagnostic_session_id: str

    @field_validator("evidence_summary")
    @classmethod
    def require_non_blank_evidence_summary(cls, value: str) -> str:
        """Require a user-readable report synthesis."""

        if not value.strip():
            raise ValueError("evidence_summary must be non-blank")
        return value

    @model_validator(mode="after")
    def validate_report_invariants(self) -> Self:
        """Enforce deterministic V2 report-shape invariants only."""

        if any(not symptom.strip() for symptom in self.reported_symptoms):
            raise ValueError("reported_symptoms entries must be non-blank")
        if any(
            not uncertainty.strip() for uncertainty in self.unresolved_uncertainties
        ):
            raise ValueError("unresolved_uncertainties entries must be non-blank")
        finding_ids = [finding.finding_id for finding in self.observed_findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("observed finding IDs must be unique")
        findings = {finding.finding_id: finding for finding in self.observed_findings}
        if self.primary_diagnosis is None:
            if self.diagnostic_outcome is DiagnosticOutcome.DIAGNOSIS_SUPPORTED:
                raise ValueError("diagnosis_supported requires a primary diagnosis")
            if any(
                finding.relationship_to_symptoms
                is DiagnosticRelevance.SUPPORTS_PRIMARY_DIAGNOSIS
                for finding in self.observed_findings
            ):
                raise ValueError("null primary diagnosis cannot have primary support")
            if not self.unresolved_uncertainties:
                raise ValueError("limited outcomes require unresolved uncertainty")
        else:
            self._validate_references(
                self.primary_diagnosis.supporting_finding_ids,
                findings,
                DiagnosticRelevance.SUPPORTS_PRIMARY_DIAGNOSIS,
                "primary diagnosis",
            )
        for factor in self.contributing_factors:
            self._validate_references(
                factor.supporting_finding_ids,
                findings,
                DiagnosticRelevance.SUPPORTED_CONTRIBUTOR,
                "contributing factor",
            )
        for alternate in self.alternate_hypotheses:
            self._validate_references(
                alternate.supporting_finding_ids, findings, None, "alternate hypothesis"
            )
        image_artifact_ids = {
            artifact_id
            for finding in self.observed_findings
            if finding.evidence_source is EvidenceSource.IMAGE
            for artifact_id in finding.artifact_ids
        }
        if not set(self.key_artifact_ids).issubset(image_artifact_ids):
            raise ValueError("key artifact IDs must be referenced by image findings")
        return self

    @staticmethod
    def _validate_references(
        references: list[str],
        findings: dict[str, ObservedFinding],
        required_relevance: DiagnosticRelevance | None,
        label: str,
    ) -> None:
        if any(reference not in findings for reference in references):
            raise ValueError(f"{label} references an unknown finding")
        if required_relevance is not None and not any(
            findings[reference].relationship_to_symptoms is required_relevance
            for reference in references
        ):
            raise ValueError(f"{label} lacks a finding with required relevance")


class CostItemType(StrEnum):
    """Cost-estimate item kind."""

    TOOL = "tool"
    PART = "part"


class PriceEstimateStatus(StrEnum):
    """Normalized price lookup outcome."""

    PRICED_LISTING_FOUND = "priced_listing_found"
    RANGE_ESTIMATE_ONLY = "range_estimate_only"
    CACHED_ESTIMATE_USED = "cached_estimate_used"
    PRICE_UNAVAILABLE = "price_unavailable"
    NEEDS_MORE_DETAIL = "needs_more_detail"


class PriceListing(APIBaseModel):
    """Observed listing evidence for a required tool or part."""

    title: str = Field(min_length=1)
    retailer: str = Field(min_length=1)
    observed_price: float = Field(ge=0)
    currency: str = "USD"
    url: str = Field(min_length=1)
    observed_at: datetime
    match_confidence: Confidence
    match_rationale: str = Field(min_length=1)


class PriceLookupRequirement(APIBaseModel):
    """Normalized planning requirement ready for pricing lookup."""

    item_type: CostItemType
    display_name: str = Field(min_length=1)
    category: str | None = None
    quantity: int = Field(default=1, ge=1)
    generic_equivalent_acceptable: bool = False
    exact_match_required: bool = False
    brand: str | None = None
    model: str | None = None
    specification: str | None = None
    compatibility_notes: str | None = None
    planning_confidence: Confidence = Confidence.UNKNOWN
    search_query: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_match_policy(self) -> Self:
        """A requirement cannot demand exact match and generic substitution."""

        if self.exact_match_required and self.generic_equivalent_acceptable:
            msg = "exact_match_required conflicts with generic_equivalent_acceptable"
            raise ValueError(msg)
        return self


class PriceLookupResult(APIBaseModel):
    """Normalized lookup result for one required item."""

    item_type: CostItemType
    requirement_name: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    status: PriceEstimateStatus
    estimate_confidence: Confidence
    looked_up_at: datetime
    estimated_price: CostEstimate | None = None
    primary_listing: PriceListing | None = None
    alternate_listings: list[PriceListing] = Field(default_factory=list, max_length=2)
    compatibility_uncertain: bool = False
    search_match_ambiguous: bool = False
    generic_substitute_used: bool = False
    exact_match_not_confirmed: bool = False

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """Keep lookup status aligned with available evidence."""

        if (
            self.status is PriceEstimateStatus.PRICED_LISTING_FOUND
            and self.primary_listing is None
        ):
            msg = "priced_listing_found requires primary_listing"
            raise ValueError(msg)
        if (
            self.status is PriceEstimateStatus.RANGE_ESTIMATE_ONLY
            and self.estimated_price is None
        ):
            msg = "range_estimate_only requires estimated_price"
            raise ValueError(msg)
        return self


class PlanCostEstimate(APIBaseModel):
    """Plan-level pricing rollup derived from item lookup results."""

    parts_total: CostEstimate
    tools_total: CostEstimate
    diy_total: CostEstimate
    items: list[PriceLookupResult]


class PartNeeded(APIBaseModel):
    """Part requirement in a plan report."""

    item: str = Field(min_length=1)
    specification: str | None = None
    quantity: int = Field(ge=1)
    required: bool
    estimated_price: CostEstimate | None = None
    price_lookup_result_id: str | None = None
    price_lookup: PriceLookupResult | None = None


class ToolNeeded(APIBaseModel):
    """Tool requirement in a plan report."""

    item: str = Field(min_length=1)
    catalog_tool_id: str | None = None
    catalog_match_confidence: Confidence | None = None
    source: Literal["catalog_match", "generated"]
    category: Literal[
        "general",
        "drivetrain",
        "brake",
        "wheel_tire",
        "torque",
        "measuring",
        "specialty",
        "other",
    ]
    action: Literal[
        "confirm_available",
        "buy",
        "borrow_recommended",
        "rent_recommended",
        "shop_only",
    ]
    quantity: int = Field(default=1, ge=1)
    unit: str | None = None
    estimated_price: CostEstimate | None = None
    notes: str | None = None
    price_lookup: PriceLookupResult | None = None


class TimeEstimate(APIBaseModel):
    """Public time estimate range."""

    min_minutes: int | None = Field(default=None, ge=0)
    max_minutes: int | None = Field(default=None, ge=0)
    confidence: Confidence
    notes: str | None = None

    @model_validator(mode="after")
    def validate_min_before_max(self) -> Self:
        """Ensure a time range is ordered when both bounds are present."""

        if (
            self.min_minutes is not None
            and self.max_minutes is not None
            and self.min_minutes > self.max_minutes
        ):
            msg = "min_minutes must be less than or equal to max_minutes"
            raise ValueError(msg)
        return self


class PlanReportV1(APIBaseModel):
    """Planning report payload."""

    schema_version: Literal["plan_report.v1"]
    diagnosis_summary: str
    parts_needed: list[PartNeeded]
    tools_needed: list[ToolNeeded]
    diy_estimate: CostEstimate
    shop_estimate: CostEstimate
    cost_estimate: PlanCostEstimate
    user_time_estimate: TimeEstimate | None = None
    shop_time_estimate: TimeEstimate | None = None
    recommendation: Literal[
        "diy_reasonable",
        "shop_recommended",
        "insufficient_info",
    ]
    recommendation_basis: str
    requires_user_decision: Literal[True]
    safety_concerns: list[SafetyFlag]


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
    payload: DiagnosticReportV1 | DiagnosticReportV2 | PlanReportV1 | dict[str, Any] = (
        Field(
            union_mode="left_to_right",
        )
    )

    @model_validator(mode="after")
    def validate_diagnostic_payload(self) -> Self:
        """Validate diagnostic report envelopes against the diagnostic payload."""

        if self.type is PhaseReportType.DIAGNOSTIC or self.schema_version in {
            "diagnostic_report.v1",
            "diagnostic_report.v2",
        }:
            if self.schema_version not in {
                "diagnostic_report.v1",
                "diagnostic_report.v2",
            }:
                msg = "diagnostic reports must use a supported diagnostic schema"
                raise ValueError(msg)
            if self.phase is not RepairSessionPhase.DIAGNOSTIC:
                msg = "diagnostic reports must use diagnostic phase"
                raise ValueError(msg)
            if self.schema_version == "diagnostic_report.v1" and not isinstance(
                self.payload, DiagnosticReportV1
            ):
                self.payload = DiagnosticReportV1.model_validate(self.payload)
            if self.schema_version == "diagnostic_report.v2" and not isinstance(
                self.payload, DiagnosticReportV2
            ):
                self.payload = DiagnosticReportV2.model_validate(self.payload)
            payload = self.payload
            if not isinstance(payload, (DiagnosticReportV1, DiagnosticReportV2)):
                msg = "diagnostic payload must use a diagnostic schema"
                raise ValueError(msg)
            if payload.schema_version != self.schema_version:
                msg = "diagnostic payload schema version must match envelope"
                raise ValueError(msg)
            if payload.safety_flags != self.safety_flags:
                msg = "diagnostic report safety flags must match envelope"
                raise ValueError(msg)
        if self.type is PhaseReportType.PLAN or self.schema_version == "plan_report.v1":
            if self.schema_version != "plan_report.v1":
                msg = "plan reports must use plan_report.v1"
                raise ValueError(msg)
            if self.phase is not RepairSessionPhase.PLANNING:
                msg = "plan reports must use planning phase"
                raise ValueError(msg)
            if not isinstance(self.payload, PlanReportV1):
                self.payload = PlanReportV1.model_validate(self.payload)
            if self.payload.safety_concerns != self.safety_flags:
                msg = "plan report safety concerns must match envelope"
                raise ValueError(msg)
        return self


class PhaseReportList(APIBaseModel):
    """Paginated phase report list."""

    items: list[PhaseReportEnvelope]
    next_cursor: str | None


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
