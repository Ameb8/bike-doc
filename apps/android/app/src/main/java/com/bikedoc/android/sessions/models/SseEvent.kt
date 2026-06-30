package com.bikedoc.android.sessions.models

import com.bikedoc.android.api.models.ArtifactRef
import com.bikedoc.android.api.models.InputRequest
import com.bikedoc.android.api.models.RepairSession
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

sealed class SseEvent {
    abstract val id: String?

    data class TurnStarted(
        override val id: String?,
        val turnId: String,
        val phase: String,
    ) : SseEvent()

    data class AssistantDelta(
        override val id: String?,
        val text: String,
    ) : SseEvent()

    data class AssistantMessageCompleted(
        override val id: String?,
        val messageId: String,
        val fullText: String,
        val artifactIds: List<String>,
    ) : SseEvent()

    data class InputRequested(
        override val id: String?,
        val inputRequest: InputRequest,
    ) : SseEvent()

    data class ArtifactReferenced(
        override val id: String?,
        val artifact: ArtifactRef,
    ) : SseEvent()

    data class PhaseReportCreated(
        override val id: String?,
        val reportId: String,
        val payload: JsonElement,
    ) : SseEvent()

    data class PhaseTransitioned(
        override val id: String?,
        val fromPhase: String,
        val toPhase: String,
        val status: String,
    ) : SseEvent()

    data class SafetyEscalated(
        override val id: String?,
        val payload: JsonElement,
    ) : SseEvent()

    data class TurnCompleted(
        override val id: String?,
        val turnId: String,
        val session: RepairSession,
    ) : SseEvent()

    data class Error(
        override val id: String?,
        val code: String,
        val message: String,
        val retryable: Boolean,
    ) : SseEvent()

    data class Heartbeat(
        override val id: String?,
    ) : SseEvent()

    data class Unknown(
        override val id: String?,
        val type: String?,
    ) : SseEvent()

    companion object {
        fun parse(
            type: String?,
            id: String?,
            data: String,
            json: Json,
        ): SseEvent =
            when (type) {
                "turn.started" -> json.decodeFromString<TurnStartedPayload>(data).toEvent(id)
                "assistant.delta" -> json.decodeFromString<AssistantDeltaPayload>(data).toEvent(id)
                "assistant.message.completed" ->
                    json.decodeFromString<AssistantMessageCompletedPayload>(data).toEvent(id)
                "input.requested" -> json.decodeFromString<InputRequestedPayload>(data).toEvent(id)
                "artifact.referenced" -> json.decodeFromString<ArtifactReferencedPayload>(data).toEvent(id)
                "phase.report.created" -> {
                    val payload = json.decodeFromString<PhaseReportCreatedPayload>(data)
                    SseEvent.PhaseReportCreated(id, payload.reportId, json.parseToJsonElement(data))
                }
                "phase.transitioned" -> json.decodeFromString<PhaseTransitionedPayload>(data).toEvent(id)
                "safety.escalated" -> SafetyEscalated(id, json.parseToJsonElement(data))
                "turn.completed" -> json.decodeFromString<TurnCompletedPayload>(data).toEvent(id)
                "error" -> json.decodeFromString<ErrorPayload>(data).toEvent(id)
                "heartbeat" -> Heartbeat(id)
                else -> Unknown(id, type)
            }
    }
}

@Serializable
private data class TurnStartedPayload(
    @SerialName("turn_id")
    val turnId: String,
    val phase: String,
) {
    fun toEvent(id: String?) = SseEvent.TurnStarted(id, turnId, phase)
}

@Serializable
private data class AssistantDeltaPayload(
    val text: String,
) {
    fun toEvent(id: String?) = SseEvent.AssistantDelta(id, text)
}

@Serializable
private data class AssistantMessageCompletedPayload(
    @SerialName("message_id")
    val messageId: String,
    @SerialName("full_text")
    val fullText: String,
    @SerialName("artifact_ids")
    val artifactIds: List<String> = emptyList(),
) {
    fun toEvent(id: String?) =
        SseEvent.AssistantMessageCompleted(
            id = id,
            messageId = messageId,
            fullText = fullText,
            artifactIds = artifactIds,
        )
}

@Serializable
private data class InputRequestedPayload(
    @SerialName("input_request")
    val inputRequest: InputRequest,
) {
    fun toEvent(id: String?) = SseEvent.InputRequested(id, inputRequest)
}

@Serializable
private data class ArtifactReferencedPayload(
    val artifact: ArtifactRef,
) {
    fun toEvent(id: String?) = SseEvent.ArtifactReferenced(id, artifact)
}

@Serializable
private data class PhaseReportCreatedPayload(
    @SerialName("report_id")
    val reportId: String,
)

@Serializable
private data class PhaseTransitionedPayload(
    @SerialName("from_phase")
    val fromPhase: String,
    @SerialName("to_phase")
    val toPhase: String,
    val status: String,
) {
    fun toEvent(id: String?) = SseEvent.PhaseTransitioned(id, fromPhase, toPhase, status)
}

@Serializable
private data class TurnCompletedPayload(
    @SerialName("turn_id")
    val turnId: String,
    val session: RepairSession,
) {
    fun toEvent(id: String?) = SseEvent.TurnCompleted(id, turnId, session)
}

@Serializable
private data class ErrorPayload(
    val code: String,
    val message: String,
    val retryable: Boolean = false,
) {
    fun toEvent(id: String?) = SseEvent.Error(id, code, message, retryable)
}
