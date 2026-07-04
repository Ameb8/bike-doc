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
