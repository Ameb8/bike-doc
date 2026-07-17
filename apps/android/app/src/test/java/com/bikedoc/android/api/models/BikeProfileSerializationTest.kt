package com.bikedoc.android.api.models

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BikeProfileSerializationTest {
    @Test
    fun `deserializes an empty bike profile v2 without inventing compatibility values`() {
        val profile =
            Json.decodeFromString<BikeProfileDto>(
                """
                {
                  "id":"bike-1","user_id":"user-1","display_name":"Commuter",
                  "has_repair_sessions":false,"schema_version":"bike_profile.v2","profile_revision":0,
                  "identity":{},"frame":{},"brakes":{"front":{},"rear":{}},"drivetrain_v2":{},
                  "rolling_system":{"front":{},"rear":{}},"suspension":{},"cockpit":{},"seating":{},
                  "electric_assist":{},"bike_type":"unknown","frame_material":"unknown",
                  "drivetrain":null,"brake_type":null,"wheel_size":null,"tire_size":null,"notes":null,
                  "created_at":"2026-07-10T12:00:00Z","updated_at":"2026-07-10T12:00:00Z"
                }
                """.trimIndent(),
            )

        assertEquals("bike_profile.v2", profile.schemaVersion)
        assertEquals(0, profile.profileRevision)
        assertNull(profile.brakeType)
        assertNull(profile.wheelSize)
        assertNull(profile.tireSize)
    }

    @Test
    fun `ignores future optional response groups while retaining known v2 fields`() {
        val profile =
            Json { ignoreUnknownKeys = true }.decodeFromString<BikeProfileDto>(
                """
                {
                  "id":"bike-1","user_id":"user-1","display_name":"Commuter","has_repair_sessions":false,
                  "schema_version":"bike_profile.v2","profile_revision":2,"identity":{"make":"Trek"},"frame":{},
                  "brakes":{"front":{},"rear":{}},"drivetrain_v2":{},"rolling_system":{"front":{},"rear":{}},
                  "suspension":{},"cockpit":{},"seating":{},"electric_assist":{},"future_optional_group":{"value":"kept server-side"},
                  "bike_type":"road","frame_material":"carbon","created_at":"2026-07-10T12:00:00Z","updated_at":"2026-07-10T12:00:00Z"
                }
                """.trimIndent(),
            )

        assertEquals("Trek", profile.identity.make)
        assertEquals(2, profile.profileRevision)
    }

    @Test
    fun `serializes supported legacy edit request fields without V2 inference metadata`() {
        val request = BikeProfilePatchDto(displayName = "Rain Bike", brakeType = "hydraulic_disc")
        val encoded = Json.encodeToString(BikeProfilePatchDto.serializer(), request)

        assertEquals("{\"display_name\":\"Rain Bike\",\"brake_type\":\"hydraulic_disc\"}", encoded)
    }
}
