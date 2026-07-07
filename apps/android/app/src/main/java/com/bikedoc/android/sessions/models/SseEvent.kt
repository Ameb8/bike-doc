package com.bikedoc.android.sessions.models

import com.bikedoc.android.api.models.ArtifactRef
import com.bikedoc.android.api.models.InputRequest
import com.bikedoc.android.api.models.RepairSession
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

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
        val reportType: String,
        val schemaVersion: String,
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
        ): SseEvent {
            val envelope = data.toRepairSessionEventEnvelope(json)
            val eventType = envelope?.type ?: type
            val eventId = id ?: envelope?.id
            val payload = envelope?.data?.toString() ?: data

            return eventType
                ?.let { EVENT_PARSERS[it]?.invoke(eventId, payload, json) }
                ?: Unknown(eventId, eventType)
        }

        private fun String.toRepairSessionEventEnvelope(json: Json): RepairSessionEventEnvelope? {
            val root = runCatching { json.parseToJsonElement(this).jsonObject }.getOrNull()
            val type = root?.get("type")?.jsonPrimitive?.content
            val envelopeData = root?.get("data")
            return if (type != null && envelopeData != null) {
                RepairSessionEventEnvelope(
                    id = root["id"]?.jsonPrimitive?.content,
                    type = type,
                    data = envelopeData,
                )
            } else {
                null
            }
        }

        private val EVENT_PARSERS: Map<String, (String?, String, Json) -> SseEvent> =
            mapOf(
                "turn.started" to { eventId, payload, json ->
                    json.decodeFromString<TurnStartedPayload>(payload).toEvent(eventId)
                },
                "assistant.delta" to { eventId, payload, json ->
                    json.decodeFromString<AssistantDeltaPayload>(payload).toEvent(eventId)
                },
                "assistant.message.completed" to { eventId, payload, json ->
                    json.decodeFromString<AssistantMessageCompletedPayload>(payload).toEvent(eventId)
                },
                "input.requested" to { eventId, payload, json ->
                    json.decodeFromString<InputRequestedPayload>(payload).toEvent(eventId)
                },
                "artifact.referenced" to { eventId, payload, json ->
                    json.decodeFromString<ArtifactReferencedPayload>(payload).toEvent(eventId)
                },
                "phase.report.created" to { eventId, payload, json ->
                    val decodedPayload = json.decodeFromString<PhaseReportCreatedPayload>(payload)
                    PhaseReportCreated(
                        id = eventId,
                        reportId = decodedPayload.reportId,
                        reportType = decodedPayload.reportType,
                        schemaVersion = decodedPayload.schemaVersion,
                        payload = json.parseToJsonElement(payload),
                    )
                },
                "phase.transitioned" to { eventId, payload, json ->
                    json.decodeFromString<PhaseTransitionedPayload>(payload).toEvent(eventId)
                },
                "safety.escalated" to { eventId, payload, json ->
                    SafetyEscalated(eventId, json.parseToJsonElement(payload))
                },
                "turn.completed" to { eventId, payload, json ->
                    json.decodeFromString<TurnCompletedPayload>(payload).toEvent(eventId)
                },
                "error" to { eventId, payload, json ->
                    json.decodeFromString<ErrorPayload>(payload).toEvent(eventId)
                },
                "heartbeat" to { eventId, _, _ -> Heartbeat(eventId) },
            )
    }
}

private data class RepairSessionEventEnvelope(
    val id: String?,
    val type: String,
    val data: JsonElement,
)

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
    @SerialName("report_type")
    val reportType: String = "",
    @SerialName("schema_version")
    val schemaVersion: String = "",
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
