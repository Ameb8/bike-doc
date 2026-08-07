package com.bikedoc.android.api

import com.bikedoc.android.api.models.AlternateHypothesisPayload
import com.bikedoc.android.api.models.AlternateHypothesisV2Payload
import com.bikedoc.android.api.models.CostEstimatePayload
import com.bikedoc.android.api.models.ContributingFactorPayload
import com.bikedoc.android.api.models.DiagnosisPayload
import com.bikedoc.android.api.models.DiagnosisV2Payload
import com.bikedoc.android.api.models.DiagnosticConfidencePayload
import com.bikedoc.android.api.models.DiagnosticOutcomePayload
import com.bikedoc.android.api.models.DiagnosticReportPayload
import com.bikedoc.android.api.models.DiagnosticReportV2Payload
import com.bikedoc.android.api.models.DiagnosticRelevancePayload
import com.bikedoc.android.api.models.DiySuitabilityPayload
import com.bikedoc.android.api.models.EvidenceSourcePayload
import com.bikedoc.android.api.models.ObservedFindingPayload
import com.bikedoc.android.api.models.PartNeededPayload
import com.bikedoc.android.api.models.PhaseReportEnvelope
import com.bikedoc.android.api.models.PlanCostEstimatePayload
import com.bikedoc.android.api.models.PlanReportPayload
import com.bikedoc.android.api.models.PriceListingPayload
import com.bikedoc.android.api.models.PriceLookupResultPayload
import com.bikedoc.android.api.models.RepairEstimatePayload
import com.bikedoc.android.api.models.RepairTimeEstimatePayload
import com.bikedoc.android.api.models.SafetyFlag
import com.bikedoc.android.api.models.ShopRepairCostEstimatePayload
import com.bikedoc.android.api.models.TimeEstimatePayload
import com.bikedoc.android.api.models.ToolNeededPayload
import com.bikedoc.android.api.models.UserSkillLevelPayload
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import timber.log.Timber
import javax.inject.Inject

interface ReportRepository {
    suspend fun getDiagnosticReport(
        sessionId: String,
        reportId: String,
    ): ApiResult<RepairReport>
}

class DefaultReportRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
        private val json: Json,
    ) : ReportRepository {
        override suspend fun getDiagnosticReport(
            sessionId: String,
            reportId: String,
        ): ApiResult<RepairReport> {
            val result: ApiResult<PhaseReportEnvelope> =
                safeApiCall {
                    apiService.getReport(sessionId, reportId)
                }
            return when (result) {
                is ApiResult.Success -> result.data.toRepairReport()
                is ApiResult.Error -> result
                ApiResult.Loading -> ApiResult.Loading
            }
        }

        private fun PhaseReportEnvelope.toRepairReport(): ApiResult<RepairReport> {
            if (!hasMatchingPayloadSchemaVersion()) {
                return ApiResult.Error(null, VERSION_ERROR_MESSAGE)
            }
            return when (type) {
                DIAGNOSTIC_TYPE ->
                    when (schemaVersion) {
                        DIAGNOSTIC_V1_SCHEMA_VERSION -> decodeDiagnosticV1Report()
                        DIAGNOSTIC_V2_SCHEMA_VERSION -> decodeDiagnosticV2Report()
                        else -> ApiResult.Error(null, VERSION_ERROR_MESSAGE)
                    }

                PLAN_TYPE ->
                    if (schemaVersion == PLAN_SCHEMA_VERSION) {
                        decodePlanReport()
                    } else {
                        ApiResult.Error(null, VERSION_ERROR_MESSAGE)
                    }

                else -> ApiResult.Error(null, VERSION_ERROR_MESSAGE)
            }
        }

        private fun PhaseReportEnvelope.hasMatchingPayloadSchemaVersion(): Boolean =
            ((payload as? JsonObject)?.get("schema_version") as? JsonPrimitive)?.content == schemaVersion

        private fun PhaseReportEnvelope.decodeDiagnosticV1Report(): ApiResult<RepairReport> =
            try {
                val payload =
                    json.decodeFromJsonElement(
                        DiagnosticReportPayload.serializer(),
                        payload,
                    )
                ApiResult.Success(payload.toDiagnosticReport(id, createdAt))
            } catch (exception: SerializationException) {
                Timber.e(exception, "Diagnostic V1 report payload decoding failed")
                ApiResult.Error(null, "Unexpected diagnostic report format.")
            } catch (exception: IllegalArgumentException) {
                Timber.e(exception, "Diagnostic V1 report payload validation failed")
                ApiResult.Error(null, exception.message ?: "Unexpected diagnostic report format.")
            }

        private fun PhaseReportEnvelope.decodeDiagnosticV2Report(): ApiResult<RepairReport> =
            try {
                val payload =
                    json.decodeFromJsonElement(
                        DiagnosticReportV2Payload.serializer(),
                        payload,
                    )
                ApiResult.Success(payload.toDiagnosticReportV2(id, createdAt))
            } catch (exception: SerializationException) {
                Timber.e(exception, "Diagnostic V2 report payload decoding failed")
                ApiResult.Error(null, "Unexpected diagnostic report format.")
            } catch (exception: IllegalArgumentException) {
                Timber.e(exception, "Diagnostic V2 report payload validation failed")
                ApiResult.Error(null, exception.message ?: "Unexpected diagnostic report format.")
            }

        private fun PhaseReportEnvelope.decodePlanReport(): ApiResult<RepairReport> =
            try {
                val payload =
                    json.decodeFromJsonElement(
                        PlanReportPayload.serializer(),
                        payload,
                    )
                ApiResult.Success(payload.toPlanReport(id, createdAt))
            } catch (exception: SerializationException) {
                Timber.e(exception, "Plan report payload decoding failed")
                ApiResult.Error(null, "Unexpected plan report format.")
            } catch (exception: IllegalArgumentException) {
                Timber.e(exception, "Plan report payload validation failed")
                ApiResult.Error(null, exception.message ?: "Unexpected plan report format.")
            }

        private fun DiagnosticReportPayload.toDiagnosticReport(
            reportId: String,
            createdAt: String,
        ): DiagnosticReport {
            require(schemaVersion == DIAGNOSTIC_V1_SCHEMA_VERSION) {
                "Unexpected diagnostic report version."
            }
            return DiagnosticReport(
                id = reportId,
                createdAt = createdAt,
                primaryDiagnosis = primaryDiagnosis.toDiagnosis(),
                alternateHypotheses = alternateHypotheses.map { it.toAlternateHypothesis() },
                evidenceSummary = evidenceSummary,
                repairEstimate = repairEstimate.toRepairEstimate(),
                userSkillLevel = userSkillLevel,
                safetyFlags = safetyFlags,
                keyArtifactIds = keyArtifactIds,
                costEstimate = costEstimate?.toPlanCostEstimate(),
            )
        }

        private fun DiagnosticReportV2Payload.toDiagnosticReportV2(
            reportId: String,
            createdAt: String,
        ): DiagnosticReportV2 {
            require(schemaVersion == DIAGNOSTIC_V2_SCHEMA_VERSION) {
                "Unexpected diagnostic report version."
            }
            return DiagnosticReportV2(
                id = reportId,
                createdAt = createdAt,
                diagnosticOutcome = diagnosticOutcome.toDiagnosticOutcome(),
                reportedSymptoms = reportedSymptoms,
                primaryDiagnosis = primaryDiagnosis?.toDiagnosisV2(),
                contributingFactors = contributingFactors.map { it.toContributingFactor() },
                observedFindings = observedFindings.map { it.toObservedFinding() },
                alternateHypotheses = alternateHypotheses.map { it.toAlternateHypothesisV2() },
                unresolvedUncertainties = unresolvedUncertainties,
                evidenceSummary = evidenceSummary,
                keyArtifactIds = keyArtifactIds,
                userSkillLevel = userSkillLevel.toUserSkillLevel(),
                safetyFlags = safetyFlags,
                diagnosticSessionId = diagnosticSessionId,
            )
        }

        private fun DiagnosticOutcomePayload.toDiagnosticOutcome(): DiagnosticOutcome =
            DiagnosticOutcome.valueOf(name)

        private fun EvidenceSourcePayload.toEvidenceSource(): EvidenceSource = EvidenceSource.valueOf(name)

        private fun DiagnosticRelevancePayload.toDiagnosticRelevance(): DiagnosticRelevance =
            DiagnosticRelevance.valueOf(name)

        private fun DiagnosticConfidencePayload.toDiagnosticConfidence(): DiagnosticConfidence =
            DiagnosticConfidence.valueOf(name)

        private fun DiySuitabilityPayload.toDiySuitability(): DiySuitability = DiySuitability.valueOf(name)

        private fun UserSkillLevelPayload.toUserSkillLevel(): UserSkillLevel = UserSkillLevel.valueOf(name)

        private fun DiagnosisV2Payload.toDiagnosisV2(): DiagnosisV2 =
            DiagnosisV2(
                component = component,
                issue = issue,
                confidence = confidence.toDiagnosticConfidence(),
                diySuitability = diySuitability.toDiySuitability(),
                supportingFindingIds = supportingFindingIds,
            )

        private fun ContributingFactorPayload.toContributingFactor(): ContributingFactor =
            ContributingFactor(
                component = component,
                issue = issue,
                confidence = confidence.toDiagnosticConfidence(),
                evidenceSummary = evidenceSummary,
                supportingFindingIds = supportingFindingIds,
            )

        private fun ObservedFindingPayload.toObservedFinding(): ObservedFinding =
            ObservedFinding(
                findingId = findingId,
                component = component,
                finding = finding,
                evidenceSource = evidenceSource.toEvidenceSource(),
                evidenceSourceDetail = evidenceSourceDetail,
                relationshipToSymptoms = relationshipToSymptoms.toDiagnosticRelevance(),
                artifactIds = artifactIds,
            )

        private fun AlternateHypothesisV2Payload.toAlternateHypothesisV2(): AlternateHypothesisV2 =
            AlternateHypothesisV2(
                component = component,
                issue = issue,
                confidence = confidence.toDiagnosticConfidence(),
                evidenceSummary = evidenceSummary,
                supportingFindingIds = supportingFindingIds,
            )

        private fun PlanReportPayload.toPlanReport(
            reportId: String,
            createdAt: String,
        ): PlanReport {
            require(schemaVersion == PLAN_SCHEMA_VERSION) {
                "Unexpected plan report version."
            }
            return PlanReport(
                id = reportId,
                createdAt = createdAt,
                diagnosisSummary = diagnosisSummary,
                partsNeeded = partsNeeded.map { it.toPartNeeded() },
                toolsNeeded = toolsNeeded.map { it.toToolNeeded() },
                diyEstimate = diyEstimate.toCostEstimate(),
                shopEstimate = shopEstimate.toCostEstimate(),
                costEstimate = costEstimate.toPlanCostEstimate(),
                userTimeEstimate = userTimeEstimate?.toTimeEstimate(),
                shopTimeEstimate = shopTimeEstimate?.toTimeEstimate(),
                recommendation = recommendation,
                recommendationBasis = recommendationBasis,
                requiresUserDecision = requiresUserDecision,
                safetyFlags = safetyConcerns,
            )
        }

        private fun PartNeededPayload.toPartNeeded(): PartNeeded =
            PartNeeded(
                item = item,
                specification = specification,
                quantity = quantity,
                required = required,
                estimatedPrice = estimatedPrice?.toCostEstimate(),
                priceLookup = priceLookup?.toPriceLookupResult(),
            )

        private fun ToolNeededPayload.toToolNeeded(): ToolNeeded =
            ToolNeeded(
                item = item,
                category = category,
                action = action,
                quantity = quantity,
                unit = unit,
                estimatedPrice = estimatedPrice?.toCostEstimate(),
                notes = notes,
                priceLookup = priceLookup?.toPriceLookupResult(),
            )

        private fun PlanCostEstimatePayload.toPlanCostEstimate(): PlanCostEstimate =
            PlanCostEstimate(
                partsTotal = partsTotal.toCostEstimate(),
                toolsTotal = toolsTotal.toCostEstimate(),
                diyTotal = diyTotal.toCostEstimate(),
                items = items.map { it.toPriceLookupResult() },
            )

        private fun PriceLookupResultPayload.toPriceLookupResult(): PriceLookupResult =
            PriceLookupResult(
                itemType = itemType,
                requirementName = requirementName,
                quantity = quantity,
                status = status,
                estimateConfidence = estimateConfidence,
                lookedUpAt = lookedUpAt,
                estimatedPrice = estimatedPrice?.toCostEstimate(),
                primaryListing = primaryListing?.toPriceListing(),
                alternateListings = alternateListings.map { it.toPriceListing() },
                compatibilityUncertain = compatibilityUncertain,
                searchMatchAmbiguous = searchMatchAmbiguous,
                genericSubstituteUsed = genericSubstituteUsed,
                exactMatchNotConfirmed = exactMatchNotConfirmed,
            )

        private fun PriceListingPayload.toPriceListing(): PriceListing =
            PriceListing(
                title = title,
                retailer = retailer,
                observedPrice = observedPrice,
                currency = currency,
                url = url,
                observedAt = observedAt,
                matchConfidence = matchConfidence,
                matchRationale = matchRationale,
            )

        private fun CostEstimatePayload.toCostEstimate(): CostEstimate =
            CostEstimate(
                currency = currency,
                minAmount = minAmount,
                maxAmount = maxAmount,
                confidence = confidence,
                source = source,
                notes = notes,
            )

        private fun TimeEstimatePayload.toTimeEstimate(): TimeEstimate =
            TimeEstimate(
                minMinutes = minMinutes,
                maxMinutes = maxMinutes,
                confidence = confidence,
                notes = notes,
            )

        private fun DiagnosisPayload.toDiagnosis(): Diagnosis =
            Diagnosis(
                component = component,
                issue = issue,
                confidence = confidence,
                diySuitability = diySuitability ?: UNKNOWN_VALUE,
            )

        private fun AlternateHypothesisPayload.toAlternateHypothesis(): AlternateHypothesis =
            AlternateHypothesis(
                component = component,
                issue = issue,
                confidence = confidence,
                ruledOutBy = ruledOutBy,
            )

        private fun RepairEstimatePayload.toRepairEstimate(): RepairEstimate =
            RepairEstimate(
                difficulty = difficulty,
                difficultyNotes = difficultyNotes,
                toolsRequired = toolsRequired,
                partsRequired = partsRequired,
                repairTime = repairTime.toRepairTimeEstimate(),
                shopRepairCost = shopRepairCost.toShopRepairCostEstimate(),
            )

        private fun RepairTimeEstimatePayload.toRepairTimeEstimate(): RepairTimeEstimate =
            RepairTimeEstimate(
                lowMinutes = lowMinutes,
                highMinutes = highMinutes,
            )

        private fun ShopRepairCostEstimatePayload.toShopRepairCostEstimate(): ShopRepairCostEstimate =
            ShopRepairCostEstimate(
                lowUsd = lowUsd,
                highUsd = highUsd,
                notes = notes,
            )

        private companion object {
            const val DIAGNOSTIC_TYPE = "diagnostic"
            const val DIAGNOSTIC_V1_SCHEMA_VERSION = "diagnostic_report.v1"
            const val DIAGNOSTIC_V2_SCHEMA_VERSION = "diagnostic_report.v2"
            const val PLAN_TYPE = "plan"
            const val PLAN_SCHEMA_VERSION = "plan_report.v1"
            const val UNKNOWN_VALUE = "unknown"
            const val VERSION_ERROR_MESSAGE = "Unsupported or mismatched report version."
        }
    }

interface RepairReport {
    val id: String
    val createdAt: String
    val safetyFlags: List<SafetyFlag>
}

data class DiagnosticReport(
    override val id: String,
    override val createdAt: String,
    val primaryDiagnosis: Diagnosis,
    val alternateHypotheses: List<AlternateHypothesis>,
    val evidenceSummary: String,
    val repairEstimate: RepairEstimate,
    val userSkillLevel: String,
    override val safetyFlags: List<SafetyFlag>,
    val keyArtifactIds: List<String>,
    val costEstimate: PlanCostEstimate?,
) : RepairReport

data class DiagnosticReportV2(
    override val id: String,
    override val createdAt: String,
    val diagnosticOutcome: DiagnosticOutcome,
    val reportedSymptoms: List<String>,
    val primaryDiagnosis: DiagnosisV2?,
    val contributingFactors: List<ContributingFactor>,
    val observedFindings: List<ObservedFinding>,
    val alternateHypotheses: List<AlternateHypothesisV2>,
    val unresolvedUncertainties: List<String>,
    val evidenceSummary: String,
    val keyArtifactIds: List<String>,
    val userSkillLevel: UserSkillLevel,
    override val safetyFlags: List<SafetyFlag>,
    val diagnosticSessionId: String,
) : RepairReport

enum class DiagnosticOutcome {
    DIAGNOSIS_SUPPORTED,
    USER_DECLINED_MORE_INPUT,
    REQUESTED_INPUT_UNAVAILABLE,
    IN_PERSON_ASSESSMENT_REQUIRED,
}

enum class DiagnosticConfidence {
    LOW,
    MEDIUM,
    HIGH,
}

enum class DiySuitability {
    UNKNOWN,
    REASONABLE,
    CAUTION,
    SHOP_RECOMMENDED,
    BLOCKED,
}

enum class UserSkillLevel {
    UNKNOWN,
    BEGINNER,
    INTERMEDIATE,
    ADVANCED,
}

data class DiagnosisV2(
    val component: String,
    val issue: String,
    val confidence: DiagnosticConfidence,
    val diySuitability: DiySuitability,
    val supportingFindingIds: List<String>,
)

data class ContributingFactor(
    val component: String,
    val issue: String,
    val confidence: DiagnosticConfidence,
    val evidenceSummary: String,
    val supportingFindingIds: List<String>,
)

data class ObservedFinding(
    val findingId: String,
    val component: String,
    val finding: String,
    val evidenceSource: EvidenceSource,
    val evidenceSourceDetail: String?,
    val relationshipToSymptoms: DiagnosticRelevance,
    val artifactIds: List<String>,
)

enum class EvidenceSource {
    IMAGE,
    USER_REPORT,
    MEASUREMENT,
    FUNCTIONAL_CHECK,
    REPAIR_HISTORY,
    OTHER,
}

enum class DiagnosticRelevance {
    UNKNOWN,
    POSSIBLE_CONTRIBUTOR,
    SUPPORTS_PRIMARY_DIAGNOSIS,
    SUPPORTED_CONTRIBUTOR,
    INCIDENTAL,
}

data class AlternateHypothesisV2(
    val component: String,
    val issue: String,
    val confidence: DiagnosticConfidence,
    val evidenceSummary: String,
    val supportingFindingIds: List<String>,
)

data class PlanReport(
    override val id: String,
    override val createdAt: String,
    val diagnosisSummary: String,
    val partsNeeded: List<PartNeeded>,
    val toolsNeeded: List<ToolNeeded>,
    val diyEstimate: CostEstimate,
    val shopEstimate: CostEstimate,
    val costEstimate: PlanCostEstimate,
    val userTimeEstimate: TimeEstimate?,
    val shopTimeEstimate: TimeEstimate?,
    val recommendation: String,
    val recommendationBasis: String,
    val requiresUserDecision: Boolean,
    override val safetyFlags: List<SafetyFlag>,
) : RepairReport

data class Diagnosis(
    val component: String,
    val issue: String,
    val confidence: String,
    val diySuitability: String,
)

data class AlternateHypothesis(
    val component: String,
    val issue: String,
    val confidence: String,
    val ruledOutBy: String?,
)

data class RepairEstimate(
    val difficulty: String,
    val difficultyNotes: String,
    val toolsRequired: List<String>,
    val partsRequired: List<String>,
    val repairTime: RepairTimeEstimate,
    val shopRepairCost: ShopRepairCostEstimate,
)

data class RepairTimeEstimate(
    val lowMinutes: Int,
    val highMinutes: Int,
)

data class ShopRepairCostEstimate(
    val lowUsd: Int,
    val highUsd: Int,
    val notes: String?,
)

data class PartNeeded(
    val item: String,
    val specification: String?,
    val quantity: Int,
    val required: Boolean,
    val estimatedPrice: CostEstimate?,
    val priceLookup: PriceLookupResult?,
)

data class ToolNeeded(
    val item: String,
    val category: String,
    val action: String,
    val quantity: Int,
    val unit: String?,
    val estimatedPrice: CostEstimate?,
    val notes: String?,
    val priceLookup: PriceLookupResult?,
)

data class PlanCostEstimate(
    val partsTotal: CostEstimate,
    val toolsTotal: CostEstimate,
    val diyTotal: CostEstimate,
    val items: List<PriceLookupResult>,
)

data class PriceLookupResult(
    val itemType: String,
    val requirementName: String,
    val quantity: Int,
    val status: String,
    val estimateConfidence: String,
    val lookedUpAt: String,
    val estimatedPrice: CostEstimate?,
    val primaryListing: PriceListing?,
    val alternateListings: List<PriceListing>,
    val compatibilityUncertain: Boolean,
    val searchMatchAmbiguous: Boolean,
    val genericSubstituteUsed: Boolean,
    val exactMatchNotConfirmed: Boolean,
)

data class PriceListing(
    val title: String,
    val retailer: String,
    val observedPrice: Double,
    val currency: String,
    val url: String,
    val observedAt: String,
    val matchConfidence: String,
    val matchRationale: String,
)

data class CostEstimate(
    val currency: String,
    val minAmount: Double?,
    val maxAmount: Double?,
    val confidence: String,
    val source: String,
    val notes: String?,
)

data class TimeEstimate(
    val minMinutes: Int?,
    val maxMinutes: Int?,
    val confidence: String,
    val notes: String?,
)
