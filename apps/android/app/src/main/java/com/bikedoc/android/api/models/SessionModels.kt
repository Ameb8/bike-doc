package com.bikedoc.android.api.models

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNames

@Serializable
data class RepairSession(
    val id: String,
    @SerialName("user_id")
    val userId: String = "",
    @SerialName("bike_id")
    val bikeId: String,
    val phase: String,
    val status: String,
    @SerialName("safety_state")
    val safetyState: String = "ok",
    @SerialName("current_input_request")
    val currentInputRequest: InputRequest? = null,
    @SerialName("execution_progress")
    val executionProgress: ExecutionProgress? = null,
    @SerialName("latest_reports")
    val latestReports: LatestReports = LatestReports(),
    @SerialName("latest_event_id")
    val latestEventId: String = "0",
    @SerialName("created_at")
    val createdAt: String,
    @SerialName("updated_at")
    val updatedAt: String,
)

@Serializable
data class RepairSessionCreate(
    @SerialName("bike_id")
    val bikeId: String,
)

@Serializable
data class RepairSessionListResponse(
    val items: List<RepairSession>,
    @SerialName("next_cursor")
    val nextCursor: String? = null,
)

@Serializable
data class InputRequest(
    val id: String,
    val type: String,
    val prompt: String = "",
    val required: Boolean = false,
    @SerialName("accepted_media_types")
    val acceptedMediaTypes: List<String> = emptyList(),
    val choices: List<InputChoice> = emptyList(),
    @SerialName("min_artifacts")
    val minArtifacts: Int? = null,
    @SerialName("max_artifacts")
    val maxArtifacts: Int? = null,
    @SerialName("created_at")
    val createdAt: String = "",
    val metadata: JsonElement? = null,
)

@Serializable
@OptIn(ExperimentalSerializationApi::class)
data class InputChoice(
    @JsonNames("value")
    val id: String,
    val label: String,
)

@Serializable
data class TurnCreate(
    @SerialName("schema_version")
    val schemaVersion: String = "ai_turn.v1",
    @SerialName("client_turn_id")
    val clientTurnId: String,
    val message: UserTurnMessage,
    @SerialName("responds_to_input_request_id")
    val respondsToInputRequestId: String? = null,
)

@Serializable
data class UserTurnMessage(
    val text: String? = null,
    @SerialName("artifact_ids")
    val artifactIds: List<String> = emptyList(),
)

@Serializable
data class TurnAccepted(
    @SerialName("turn_id")
    val turnId: String,
    @SerialName("repair_session_id")
    val repairSessionId: String,
    @SerialName("start_event_id")
    val startEventId: String,
    @SerialName("event_stream_url")
    val eventStreamUrl: String,
    val session: RepairSession,
)

@Serializable
data class ArtifactRef(
    val id: String,
    @SerialName("content_type")
    val contentType: String? = null,
    val purpose: String? = null,
)

@Serializable
data class ExecutionProgress(
    @SerialName("current_step_index")
    val currentStepIndex: Int? = null,
    @SerialName("total_steps")
    val totalSteps: Int? = null,
)

@Serializable
data class LatestReports(
    @SerialName("diagnostic_report_id")
    val diagnosticReportId: String? = null,
    @SerialName("plan_report_id")
    val planReportId: String? = null,
    @SerialName("execution_report_id")
    val executionReportId: String? = null,
    @SerialName("shop_referral_report_id")
    val shopReferralReportId: String? = null,
)
