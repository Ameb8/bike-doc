package com.bikedoc.android.api.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class PhaseReportList(
    val items: List<PhaseReportEnvelope>,
    @SerialName("next_cursor")
    val nextCursor: String? = null,
)

@Serializable
data class PhaseReportEnvelope(
    val id: String,
    @SerialName("repair_session_id")
    val repairSessionId: String,
    val type: String = "",
    @SerialName("schema_version")
    val schemaVersion: String = "",
    val phase: String,
    val summary: String = "",
    @SerialName("safety_flags")
    val safetyFlags: List<SafetyFlag> = emptyList(),
    @SerialName("source_artifact_ids")
    val sourceArtifactIds: List<String> = emptyList(),
    val payload: JsonElement,
    @SerialName("created_at")
    val createdAt: String,
)

@Serializable
data class DiagnosticReportPayload(
    @SerialName("schema_version")
    val schemaVersion: String,
    @SerialName("primary_diagnosis")
    val primaryDiagnosis: DiagnosisPayload,
    @SerialName("alternate_hypotheses")
    val alternateHypotheses: List<AlternateHypothesisPayload> = emptyList(),
    @SerialName("evidence_summary")
    val evidenceSummary: String,
    @SerialName("repair_estimate")
    val repairEstimate: RepairEstimatePayload,
    @SerialName("key_artifact_ids")
    val keyArtifactIds: List<String> = emptyList(),
    @SerialName("user_skill_level")
    val userSkillLevel: String,
    @SerialName("safety_flags")
    val safetyFlags: List<SafetyFlag> = emptyList(),
    @SerialName("diagnostic_session_id")
    val diagnosticSessionId: String,
    @SerialName("cost_estimate")
    val costEstimate: PlanCostEstimatePayload? = null,
)

@Serializable
data class DiagnosticReportV2Payload(
    @SerialName("schema_version")
    val schemaVersion: String,
    @SerialName("diagnostic_outcome")
    val diagnosticOutcome: DiagnosticOutcomePayload,
    @SerialName("reported_symptoms")
    val reportedSymptoms: List<String>,
    @SerialName("primary_diagnosis")
    val primaryDiagnosis: DiagnosisV2Payload?,
    @SerialName("contributing_factors")
    val contributingFactors: List<ContributingFactorPayload>,
    @SerialName("observed_findings")
    val observedFindings: List<ObservedFindingPayload>,
    @SerialName("alternate_hypotheses")
    val alternateHypotheses: List<AlternateHypothesisV2Payload>,
    @SerialName("unresolved_uncertainties")
    val unresolvedUncertainties: List<String>,
    @SerialName("evidence_summary")
    val evidenceSummary: String,
    @SerialName("key_artifact_ids")
    val keyArtifactIds: List<String>,
    @SerialName("user_skill_level")
    val userSkillLevel: UserSkillLevelPayload,
    @SerialName("safety_flags")
    val safetyFlags: List<SafetyFlag>,
    @SerialName("diagnostic_session_id")
    val diagnosticSessionId: String,
)

@Serializable
enum class DiagnosticOutcomePayload {
    @SerialName("diagnosis_supported")
    DIAGNOSIS_SUPPORTED,

    @SerialName("user_declined_more_input")
    USER_DECLINED_MORE_INPUT,

    @SerialName("requested_input_unavailable")
    REQUESTED_INPUT_UNAVAILABLE,

    @SerialName("in_person_assessment_required")
    IN_PERSON_ASSESSMENT_REQUIRED,
}

@Serializable
enum class EvidenceSourcePayload {
    @SerialName("image")
    IMAGE,

    @SerialName("user_report")
    USER_REPORT,

    @SerialName("measurement")
    MEASUREMENT,

    @SerialName("functional_check")
    FUNCTIONAL_CHECK,

    @SerialName("repair_history")
    REPAIR_HISTORY,

    @SerialName("other")
    OTHER,
}

@Serializable
enum class DiagnosticRelevancePayload {
    @SerialName("unknown")
    UNKNOWN,

    @SerialName("possible_contributor")
    POSSIBLE_CONTRIBUTOR,

    @SerialName("supports_primary_diagnosis")
    SUPPORTS_PRIMARY_DIAGNOSIS,

    @SerialName("supported_contributor")
    SUPPORTED_CONTRIBUTOR,

    @SerialName("incidental")
    INCIDENTAL,
}

@Serializable
enum class DiagnosticConfidencePayload {
    @SerialName("low")
    LOW,

    @SerialName("medium")
    MEDIUM,

    @SerialName("high")
    HIGH,
}

@Serializable
enum class DiySuitabilityPayload {
    @SerialName("unknown")
    UNKNOWN,

    @SerialName("reasonable")
    REASONABLE,

    @SerialName("caution")
    CAUTION,

    @SerialName("shop_recommended")
    SHOP_RECOMMENDED,

    @SerialName("blocked")
    BLOCKED,
}

@Serializable
enum class UserSkillLevelPayload {
    @SerialName("unknown")
    UNKNOWN,

    @SerialName("beginner")
    BEGINNER,

    @SerialName("intermediate")
    INTERMEDIATE,

    @SerialName("advanced")
    ADVANCED,
}

@Serializable
data class DiagnosisV2Payload(
    val component: String,
    val issue: String,
    val confidence: DiagnosticConfidencePayload,
    @SerialName("diy_suitability")
    val diySuitability: DiySuitabilityPayload,
    @SerialName("supporting_finding_ids")
    val supportingFindingIds: List<String>,
)

@Serializable
data class ContributingFactorPayload(
    val component: String,
    val issue: String,
    val confidence: DiagnosticConfidencePayload,
    @SerialName("evidence_summary")
    val evidenceSummary: String,
    @SerialName("supporting_finding_ids")
    val supportingFindingIds: List<String>,
)

@Serializable
data class ObservedFindingPayload(
    @SerialName("finding_id")
    val findingId: String,
    val component: String,
    val finding: String,
    @SerialName("evidence_source")
    val evidenceSource: EvidenceSourcePayload,
    @SerialName("evidence_source_detail")
    val evidenceSourceDetail: String?,
    @SerialName("relationship_to_symptoms")
    val relationshipToSymptoms: DiagnosticRelevancePayload,
    @SerialName("artifact_ids")
    val artifactIds: List<String>,
)

@Serializable
data class AlternateHypothesisV2Payload(
    val component: String,
    val issue: String,
    val confidence: DiagnosticConfidencePayload,
    @SerialName("evidence_summary")
    val evidenceSummary: String,
    @SerialName("supporting_finding_ids")
    val supportingFindingIds: List<String>,
)

@Serializable
data class PlanReportPayload(
    @SerialName("schema_version")
    val schemaVersion: String,
    @SerialName("diagnosis_summary")
    val diagnosisSummary: String,
    @SerialName("parts_needed")
    val partsNeeded: List<PartNeededPayload> = emptyList(),
    @SerialName("tools_needed")
    val toolsNeeded: List<ToolNeededPayload> = emptyList(),
    @SerialName("diy_estimate")
    val diyEstimate: CostEstimatePayload,
    @SerialName("shop_estimate")
    val shopEstimate: CostEstimatePayload,
    @SerialName("cost_estimate")
    val costEstimate: PlanCostEstimatePayload,
    @SerialName("user_time_estimate")
    val userTimeEstimate: TimeEstimatePayload? = null,
    @SerialName("shop_time_estimate")
    val shopTimeEstimate: TimeEstimatePayload? = null,
    val recommendation: String,
    @SerialName("recommendation_basis")
    val recommendationBasis: String,
    @SerialName("requires_user_decision")
    val requiresUserDecision: Boolean,
    @SerialName("safety_concerns")
    val safetyConcerns: List<SafetyFlag> = emptyList(),
)

@Serializable
data class PartNeededPayload(
    val item: String,
    val specification: String? = null,
    val quantity: Int,
    val required: Boolean,
    @SerialName("estimated_price")
    val estimatedPrice: CostEstimatePayload? = null,
    @SerialName("price_lookup_result_id")
    val priceLookupResultId: String? = null,
    @SerialName("price_lookup")
    val priceLookup: PriceLookupResultPayload? = null,
)

@Serializable
data class ToolNeededPayload(
    val item: String,
    @SerialName("catalog_tool_id")
    val catalogToolId: String? = null,
    @SerialName("catalog_match_confidence")
    val catalogMatchConfidence: String? = null,
    val source: String,
    val category: String,
    val action: String,
    val quantity: Int = 1,
    val unit: String? = null,
    @SerialName("estimated_price")
    val estimatedPrice: CostEstimatePayload? = null,
    val notes: String? = null,
    @SerialName("price_lookup")
    val priceLookup: PriceLookupResultPayload? = null,
)

@Serializable
data class PlanCostEstimatePayload(
    @SerialName("parts_total")
    val partsTotal: CostEstimatePayload,
    @SerialName("tools_total")
    val toolsTotal: CostEstimatePayload,
    @SerialName("diy_total")
    val diyTotal: CostEstimatePayload,
    val items: List<PriceLookupResultPayload> = emptyList(),
)

@Serializable
data class PriceLookupResultPayload(
    @SerialName("item_type")
    val itemType: String,
    @SerialName("requirement_name")
    val requirementName: String,
    val quantity: Int,
    val status: String,
    @SerialName("estimate_confidence")
    val estimateConfidence: String,
    @SerialName("looked_up_at")
    val lookedUpAt: String,
    @SerialName("estimated_price")
    val estimatedPrice: CostEstimatePayload? = null,
    @SerialName("primary_listing")
    val primaryListing: PriceListingPayload? = null,
    @SerialName("alternate_listings")
    val alternateListings: List<PriceListingPayload> = emptyList(),
    @SerialName("compatibility_uncertain")
    val compatibilityUncertain: Boolean = false,
    @SerialName("search_match_ambiguous")
    val searchMatchAmbiguous: Boolean = false,
    @SerialName("generic_substitute_used")
    val genericSubstituteUsed: Boolean = false,
    @SerialName("exact_match_not_confirmed")
    val exactMatchNotConfirmed: Boolean = false,
)

@Serializable
data class PriceListingPayload(
    val title: String,
    val retailer: String,
    @SerialName("observed_price")
    val observedPrice: Double,
    val currency: String = "USD",
    val url: String,
    @SerialName("observed_at")
    val observedAt: String,
    @SerialName("match_confidence")
    val matchConfidence: String,
    @SerialName("match_rationale")
    val matchRationale: String,
)

@Serializable
data class CostEstimatePayload(
    val currency: String = "USD",
    @SerialName("min_amount")
    val minAmount: Double? = null,
    @SerialName("max_amount")
    val maxAmount: Double? = null,
    val confidence: String,
    val source: String,
    val notes: String? = null,
)

@Serializable
data class TimeEstimatePayload(
    @SerialName("min_minutes")
    val minMinutes: Int? = null,
    @SerialName("max_minutes")
    val maxMinutes: Int? = null,
    val confidence: String,
    val notes: String? = null,
)

@Serializable
data class DiagnosisPayload(
    val component: String,
    val issue: String,
    val confidence: String,
    @SerialName("diy_suitability")
    val diySuitability: String? = null,
)

@Serializable
data class AlternateHypothesisPayload(
    val component: String,
    val issue: String,
    val confidence: String,
    @SerialName("ruled_out_by")
    val ruledOutBy: String? = null,
)

@Serializable
data class RepairEstimatePayload(
    val difficulty: String,
    @SerialName("difficulty_notes")
    val difficultyNotes: String,
    @SerialName("tools_required")
    val toolsRequired: List<String> = emptyList(),
    @SerialName("parts_required")
    val partsRequired: List<String> = emptyList(),
    @SerialName("repair_time")
    val repairTime: RepairTimeEstimatePayload,
    @SerialName("shop_repair_cost")
    val shopRepairCost: ShopRepairCostEstimatePayload,
)

@Serializable
data class RepairTimeEstimatePayload(
    @SerialName("low_minutes")
    val lowMinutes: Int,
    @SerialName("high_minutes")
    val highMinutes: Int,
)

@Serializable
data class ShopRepairCostEstimatePayload(
    @SerialName("low_usd")
    val lowUsd: Int,
    @SerialName("high_usd")
    val highUsd: Int,
    val notes: String? = null,
)

@Serializable
data class SafetyFlag(
    val code: String,
    val severity: String,
    val phase: String,
    val message: String,
    @SerialName("blocks_repair_instructions")
    val blocksRepairInstructions: Boolean,
)
