"""Report API schemas and mappers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
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
    payload: DiagnosticReportV1 | PlanReportV1 | dict[str, Any] = Field(
        union_mode="left_to_right",
    )

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
