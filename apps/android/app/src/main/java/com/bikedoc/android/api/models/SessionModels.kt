package com.bikedoc.android.api.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class RepairSession(
    val id: String,
    @SerialName("bike_id")
    val bikeId: String,
    val phase: String,
    val status: String,
    @SerialName("current_input_request")
    val currentInputRequest: InputRequest? = null,
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
    val id: String? = null,
    val type: String,
    val prompt: String? = null,
    val choices: List<InputChoice> = emptyList(),
    @SerialName("min_artifacts")
    val minArtifacts: Int? = null,
    val metadata: JsonElement? = null,
)

@Serializable
data class InputChoice(
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
    @SerialName("start_event_id")
    val startEventId: String? = null,
)

@Serializable
data class ArtifactRef(
    val id: String,
    @SerialName("content_type")
    val contentType: String? = null,
    val purpose: String? = null,
)
