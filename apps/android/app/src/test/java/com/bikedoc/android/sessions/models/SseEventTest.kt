package com.bikedoc.android.sessions.models

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SseEventTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun parsesAssistantDelta() {
        val event =
            SseEvent.parse(
                type = "assistant.delta",
                id = "event-1",
                data = """{"text":"Check the rear derailleur."}""",
                json = json,
            )

        assertTrue(event is SseEvent.AssistantDelta)
        val delta = event as SseEvent.AssistantDelta
        assertEquals("event-1", delta.id)
        assertEquals("Check the rear derailleur.", delta.text)
    }

    @Test
    fun parsesAssistantDeltaFromRepairSessionEventEnvelope() {
        val event =
            SseEvent.parse(
                type = "assistant.delta",
                id = "1",
                data =
                    """
                    {
                      "id": "1",
                      "session_id": "session-1",
                      "turn_id": "turn-1",
                      "type": "assistant.delta",
                      "sequence": 1,
                      "created_at": "2026-07-02T00:00:00Z",
                      "data": {"text":"Check the rear derailleur."}
                    }
                    """.trimIndent(),
                json = json,
            )

        assertTrue(event is SseEvent.AssistantDelta)
        val delta = event as SseEvent.AssistantDelta
        assertEquals("1", delta.id)
        assertEquals("Check the rear derailleur.", delta.text)
    }

    @Test
    fun parsesInputRequestedEnvelopeWithBackendChoiceValues() {
        val event =
            SseEvent.parse(
                type = "input.requested",
                id = "2",
                data =
                    """
                    {
                      "id": "2",
                      "session_id": "session-1",
                      "turn_id": "turn-1",
                      "type": "input.requested",
                      "sequence": 2,
                      "created_at": "2026-07-02T00:00:01Z",
                      "data": {
                        "input_request": {
                          "id": "request-1",
                          "type": "decision",
                          "prompt": "Continue?",
                          "choices": [{"value": "yes", "label": "Yes"}]
                        }
                      }
                    }
                    """.trimIndent(),
                json = json,
            )

        assertTrue(event is SseEvent.InputRequested)
        val inputRequested = event as SseEvent.InputRequested
        assertEquals("yes", inputRequested.inputRequest.choices.single().id)
        assertEquals("Yes", inputRequested.inputRequest.choices.single().label)
    }

    @Test
    fun preservesUnknownEventTypesForViewModelToIgnore() {
        val event =
            SseEvent.parse(
                type = "future.event",
                id = "event-2",
                data = "{}",
                json = json,
            )

        assertTrue(event is SseEvent.Unknown)
        val unknown = event as SseEvent.Unknown
        assertEquals("event-2", unknown.id)
        assertEquals("future.event", unknown.type)
    }
}
