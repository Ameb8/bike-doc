package com.bikedoc.android.sessions.models

import kotlinx.serialization.json.JsonElement

data class BikeSession(
    val id: String,
    val userId: String = "",
    val bikeId: String,
    val workflow: String,
    val phase: String,
    val status: String,
    val safetyState: String = "ok",
    val currentInputRequest: InputRequest? = null,
    val executionProgress: ExecutionProgress? = null,
    val latestReports: LatestReports = LatestReports(),
    val latestEventId: String = "0",
    val createdAt: String,
    val updatedAt: String,
)

data class BikeSessionCreate(
    val workflow: String,
    val bikeId: String,
)

data class BikeSessionListResponse(
    val items: List<BikeSession>,
    val nextCursor: String? = null,
)

data class InputRequest(
    val id: String,
    val type: String,
    val prompt: String = "",
    val required: Boolean = false,
    val acceptedMediaTypes: List<String> = emptyList(),
    val choices: List<InputChoice> = emptyList(),
    val minArtifacts: Int? = null,
    val maxArtifacts: Int? = null,
    val createdAt: String = "",
    val metadata: JsonElement? = null,
)

data class InputChoice(
    val id: String,
    val label: String,
)

data class TurnCreate(
    val schemaVersion: String = "ai_turn.v1",
    val clientTurnId: String,
    val message: UserTurnMessage,
    val respondsToInputRequestId: String? = null,
)

data class UserTurnMessage(
    val text: String? = null,
    val artifactIds: List<String> = emptyList(),
)

data class TurnAccepted(
    val turnId: String,
    val sessionId: String,
    val startEventId: String,
    val eventStreamUrl: String,
    val session: BikeSession,
)

data class ArtifactRef(
    val id: String,
    val userId: String = "",
    val sessionId: String? = null,
    val bikeId: String? = null,
    val purpose: String = "",
    val mediaType: String = "",
    val mimeType: String = "",
    val filename: String = "",
    val byteSize: Long = 0,
    val width: Int? = null,
    val height: Int? = null,
    val durationSeconds: Double? = null,
    val status: String = "",
    val rejectionReason: String? = null,
    val createdAt: String = "",
)

data class ExecutionProgress(
    val currentStepIndex: Int? = null,
    val totalSteps: Int? = null,
)

data class LatestReports(
    val diagnosticReportId: String? = null,
    val planReportId: String? = null,
    val executionReportId: String? = null,
    val shopReferralReportId: String? = null,
)
