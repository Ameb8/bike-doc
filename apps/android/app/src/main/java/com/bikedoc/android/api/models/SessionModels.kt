package com.bikedoc.android.api.models

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNames

@Serializable
data class BikeSessionDto(
    val id: String,
    @SerialName("user_id")
    val userId: String = "",
    @SerialName("bike_id")
    val bikeId: String,
    val workflow: String,
    val phase: String,
    val status: String,
    @SerialName("safety_state")
    val safetyState: String = "ok",
    @SerialName("current_input_request")
    val currentInputRequest: InputRequestDto? = null,
    @SerialName("execution_progress")
    val executionProgress: ExecutionProgressDto? = null,
    @SerialName("latest_reports")
    val latestReports: LatestReportsDto = LatestReportsDto(),
    @SerialName("latest_event_id")
    val latestEventId: String = "0",
    @SerialName("created_at")
    val createdAt: String,
    @SerialName("updated_at")
    val updatedAt: String,
)

@Serializable
data class BikeSessionCreateDto(
    val workflow: String,
    @SerialName("bike_id")
    val bikeId: String,
)

@Serializable
data class BikeSessionListResponseDto(
    val items: List<BikeSessionDto>,
    @SerialName("next_cursor")
    val nextCursor: String? = null,
)

@Serializable
data class InputRequestDto(
    val id: String,
    val type: String,
    val prompt: String = "",
    val required: Boolean = false,
    @SerialName("accepted_media_types")
    val acceptedMediaTypes: List<String> = emptyList(),
    val choices: List<InputChoiceDto> = emptyList(),
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
data class InputChoiceDto(
    @JsonNames("value")
    val id: String,
    val label: String,
)

@Serializable
data class TurnCreateDto(
    @SerialName("schema_version")
    val schemaVersion: String = "ai_turn.v1",
    @SerialName("client_turn_id")
    val clientTurnId: String,
    val message: UserTurnMessageDto,
    @SerialName("responds_to_input_request_id")
    val respondsToInputRequestId: String? = null,
)

@Serializable
data class UserTurnMessageDto(
    val text: String? = null,
    @SerialName("artifact_ids")
    val artifactIds: List<String> = emptyList(),
)

@Serializable
data class TurnAcceptedDto(
    @SerialName("turn_id")
    val turnId: String,
    @SerialName("session_id")
    val sessionId: String,
    @SerialName("start_event_id")
    val startEventId: String,
    @SerialName("event_stream_url")
    val eventStreamUrl: String,
    val session: BikeSessionDto,
)

@Serializable
data class ArtifactRefDto(
    val id: String,
    @SerialName("user_id")
    val userId: String = "",
    @SerialName("session_id")
    val sessionId: String? = null,
    @SerialName("bike_id")
    val bikeId: String? = null,
    val purpose: String = "",
    @SerialName("media_type")
    val mediaType: String = "",
    @SerialName("mime_type")
    val mimeType: String = "",
    val filename: String = "",
    @SerialName("byte_size")
    val byteSize: Long = 0,
    val width: Int? = null,
    val height: Int? = null,
    @SerialName("duration_seconds")
    val durationSeconds: Double? = null,
    val status: String = "",
    @SerialName("rejection_reason")
    val rejectionReason: String? = null,
    @SerialName("created_at")
    val createdAt: String = "",
)

@Serializable
data class ArtifactUploadResponse(
    val artifact: ArtifactRefDto,
)

data class PreparedDiagnosticPhoto(
    val bytes: ByteArray,
    val fileName: String,
    val mimeType: String,
    val clientArtifactId: String? = null,
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (javaClass != other?.javaClass) return false

        other as PreparedDiagnosticPhoto

        if (!bytes.contentEquals(other.bytes)) return false
        if (fileName != other.fileName) return false
        if (mimeType != other.mimeType) return false
        if (clientArtifactId != other.clientArtifactId) return false

        return true
    }

    override fun hashCode(): Int {
        var result = bytes.contentHashCode()
        result = 31 * result + fileName.hashCode()
        result = 31 * result + mimeType.hashCode()
        result = 31 * result + (clientArtifactId?.hashCode() ?: 0)
        return result
    }
}

@Serializable
data class ExecutionProgressDto(
    @SerialName("current_step_index")
    val currentStepIndex: Int? = null,
    @SerialName("total_steps")
    val totalSteps: Int? = null,
)

@Serializable
data class LatestReportsDto(
    @SerialName("diagnostic_report_id")
    val diagnosticReportId: String? = null,
    @SerialName("plan_report_id")
    val planReportId: String? = null,
    @SerialName("execution_report_id")
    val executionReportId: String? = null,
    @SerialName("shop_referral_report_id")
    val shopReferralReportId: String? = null,
)
