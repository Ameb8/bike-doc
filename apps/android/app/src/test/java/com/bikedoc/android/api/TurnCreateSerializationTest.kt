package com.bikedoc.android.api

import com.bikedoc.android.api.models.TurnCreate
import com.bikedoc.android.api.models.UserTurnMessage
import com.bikedoc.android.di.CoreModule
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

class TurnCreateSerializationTest {
    @Test
    fun `turn create serialization includes backend-required default fields`() {
        val json = CoreModule.provideJson()

        val payload =
            json.parseToJsonElement(
                json.encodeToString(
                    TurnCreate(
                        clientTurnId = "turn-1",
                        message = UserTurnMessage(text = "The chain skips under load."),
                    ),
                ),
            ).jsonObject

        assertEquals("ai_turn.v1", payload.getValue("schema_version").jsonPrimitive.content)
        assertEquals(
            emptyList<String>(),
            payload
                .getValue("message")
                .jsonObject
                .getValue("artifact_ids")
                .jsonArray
                .map { it.jsonPrimitive.content },
        )
    }
}
