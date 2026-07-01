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
