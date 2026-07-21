package com.bikedoc.android.bikes

import com.bikedoc.android.api.models.BikeBrakesDto
import com.bikedoc.android.api.models.BikeCockpitDto
import com.bikedoc.android.api.models.BikeDrivetrainDto
import com.bikedoc.android.api.models.BikeElectricAssistDto
import com.bikedoc.android.api.models.BikeFrameDto
import com.bikedoc.android.api.models.BikeIdentityDto
import com.bikedoc.android.api.models.BikeProfileDto
import com.bikedoc.android.api.models.BikeRollingSystemDto
import com.bikedoc.android.api.models.BikeSeatingDto
import com.bikedoc.android.api.models.BikeSuspensionDto
import com.bikedoc.android.api.models.BrakeAssemblyDto
import com.bikedoc.android.api.models.TireComponentDto
import com.bikedoc.android.api.models.WheelPositionDto
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class BikeProfileMapperTest {
    @Test
    fun `create request contains only a display name when no optional fields are supplied`() {
        val request = BikeProfileEdit(displayName = "Commuter").toCreateRequest()

        assertEquals(setOf("display_name"), request.keys)
        assertEquals("Commuter", request["display_name"].toString().trim('"'))
    }

    @Test
    fun `patch omits untouched fields`() {
        val original = BikeProfileEdit(displayName = "Commuter", frame = BikeFrame(material = "aluminum"))

        val request = original.copy(displayName = "Rain Bike").toPatchRequest(original)

        assertEquals(setOf("display_name"), request.keys)
        assertFalse("frame" in request)
    }

    @Test
    fun `patch sends explicit null when a user clears a loaded technical value`() {
        val original = BikeProfileEdit(displayName = "Commuter", frame = BikeFrame(material = "aluminum"))

        val request = original.copy(frame = BikeFrame(material = null)).toPatchRequest(original)

        assertEquals(JsonNull, request["frame"]!!.jsonObject["material"])
    }

    @Test
    fun `preserves migrated legacy compatibility values when structured groups are empty`() {
        val profile = profileDto(make = "Surly", drivetrain = "1x11", brakeType = "mechanical_disc").toDomain()

        assertEquals("Surly", profile.legacy.make)
        assertEquals("1x11", profile.legacy.drivetrain)
        assertEquals("mechanical_disc", profile.legacy.brakeType)
        assertNull(profile.brakes.front.mechanism)
    }

    @Test
    fun `maps explicit component absence separately from unknown and preserves null legacy aggregates`() {
        val profile =
            profileDto(
                brakes =
                    BikeBrakesDto(
                        front = BrakeAssemblyDto(presence = "absent"),
                        rear = BrakeAssemblyDto(presence = "unknown", mechanism = null),
                    ),
            ).toDomain()

        assertEquals(ComponentPresence.ABSENT, profile.brakes.front.component.presence)
        assertEquals(ComponentPresence.UNKNOWN, profile.brakes.rear.component.presence)
        assertNull(profile.legacy.brakeType)
    }

    @Test
    fun `keeps API compatibility summaries null for mixed positioned wheel and tire facts`() {
        val profile =
            profileDto(
                rollingSystem =
                    BikeRollingSystemDto(
                        front = WheelPositionDto(tire = TireComponentDto(markedSize = "700x32")),
                        rear = WheelPositionDto(tire = TireComponentDto(markedSize = "650bx47")),
                    ),
            ).toDomain()

        assertEquals("700x32", profile.rollingSystem.front.tire?.markedSize)
        assertEquals("650bx47", profile.rollingSystem.rear.tire?.markedSize)
        assertNull(profile.legacy.wheelSize)
        assertNull(profile.legacy.tireSize)
    }

    @Test
    fun `preserves complete structured drivetrain and electric assist facts`() {
        val profile =
            profileDto(
                drivetrainV2 =
                    BikeDrivetrainDto(
                        architecture = "derailleur",
                        driveMedium = "chain",
                        rearSpeedCount = 12,
                    ),
                electricAssist = BikeElectricAssistDto(presence = "present", systemManufacturer = "Bosch"),
            ).toDomain()

        assertEquals("derailleur", profile.drivetrain.architecture)
        assertEquals(12, profile.drivetrain.rearSpeedCount)
        assertEquals(ComponentPresence.PRESENT, profile.electricAssist.presence)
        assertEquals("Bosch", profile.electricAssist.systemManufacturer)
    }
}

private fun profileDto(
    make: String? = null,
    drivetrain: String? = null,
    brakeType: String? = null,
    brakes: BikeBrakesDto = BikeBrakesDto(),
    drivetrainV2: BikeDrivetrainDto = BikeDrivetrainDto(),
    rollingSystem: BikeRollingSystemDto = BikeRollingSystemDto(),
    electricAssist: BikeElectricAssistDto = BikeElectricAssistDto(),
) = BikeProfileDto(
    id = "bike-1", userId = "user-1", displayName = "Commuter", hasRepairSessions = false,
    schemaVersion = "bike_profile.v2", profileRevision = 4, identity = BikeIdentityDto(), frame = BikeFrameDto(),
    brakes = brakes, drivetrainV2 = drivetrainV2, rollingSystem = rollingSystem, suspension = BikeSuspensionDto(),
    cockpit = BikeCockpitDto(), seating = BikeSeatingDto(), electricAssist = electricAssist, bikeType = "unknown",
    make = make, drivetrain = drivetrain, brakeType = brakeType, frameMaterial = "unknown",
    createdAt = "2026-07-10T12:00:00Z", updatedAt = "2026-07-10T12:00:00Z",
)
