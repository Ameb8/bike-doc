package com.bikedoc.android.api

import com.bikedoc.android.api.models.BikeSessionCreateDto
import com.bikedoc.android.api.models.TurnAcceptedDto
import com.bikedoc.android.di.CoreModule
import com.bikedoc.android.sessions.models.SseEvent
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BikeSessionContractTest {
    private val json = CoreModule.provideJson()

    @Test
    fun `create includes explicit repair workflow`() {
        val body = json.parseToJsonElement(json.encodeToString(BikeSessionCreateDto("repair", "bike-1"))).jsonObject
        assertEquals("repair", body.getValue("workflow").jsonPrimitive.content)
        assertEquals("bike-1", body.getValue("bike_id").jsonPrimitive.content)
    }

    @Test
    fun `turn acceptance and stream snapshot decode generic session`() {
        val session =
            """
            {"id":"ses_123","bike_id":"bike-1","workflow":"repair","phase":"diagnostic",
             "status":"running","created_at":"now","updated_at":"now"}
            """.trimIndent()
        val accepted =
            json.decodeFromString<TurnAcceptedDto>(
                """{"turn_id":"turn-1","session_id":"ses_123","start_event_id":"1",
                    "event_stream_url":"/v1/sessions/ses_123/events","session":$session}""",
            ).toDomain()
        assertEquals("ses_123", accepted.sessionId)
        assertEquals("repair", accepted.session.workflow)
        val event = SseEvent.parse("turn.completed", "2", """{"turn_id":"turn-1","session":$session}""", json)
        assertTrue(event is SseEvent.TurnCompleted)
        assertEquals("ses_123", (event as SseEvent.TurnCompleted).session.id)
    }
}
