@file:Suppress("MaxLineLength")

package com.bikedoc.android.bikes

import com.bikedoc.android.api.models.BikeBrakesDto
import com.bikedoc.android.api.models.BikeCockpitDto
import com.bikedoc.android.api.models.BikeDrivetrainDto
import com.bikedoc.android.api.models.BikeElectricAssistDto
import com.bikedoc.android.api.models.BikeFrameDto
import com.bikedoc.android.api.models.BikeIdentityDto
import com.bikedoc.android.api.models.BikeProfileCreateDto
import com.bikedoc.android.api.models.BikeProfileDto
import com.bikedoc.android.api.models.BikeProfilePatchDto
import com.bikedoc.android.api.models.BikeRollingSystemDto
import com.bikedoc.android.api.models.BikeSeatingDto
import com.bikedoc.android.api.models.BikeSuspensionDto
import com.bikedoc.android.api.models.BrakeAssemblyDto
import com.bikedoc.android.api.models.BrakeUnitDto
import com.bikedoc.android.api.models.ComponentIdentityDto
import com.bikedoc.android.api.models.DrivetrainRoleDto
import com.bikedoc.android.api.models.ElectricBatteryDto
import com.bikedoc.android.api.models.ElectricMotorDto
import com.bikedoc.android.api.models.ForkDto
import com.bikedoc.android.api.models.HandlebarDto
import com.bikedoc.android.api.models.HeadsetDto
import com.bikedoc.android.api.models.HubComponentDto
import com.bikedoc.android.api.models.RimComponentDto
import com.bikedoc.android.api.models.RotorDto
import com.bikedoc.android.api.models.SeatpostDto
import com.bikedoc.android.api.models.StemDto
import com.bikedoc.android.api.models.TireComponentDto
import com.bikedoc.android.api.models.WheelComponentDto
import com.bikedoc.android.api.models.WheelPositionDto
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

internal fun BikeProfileDto.toDomain() =
    BikeProfile(
        id = id,
        userId = userId,
        displayName = displayName,
        hasRepairSessions = hasRepairSessions,
        schemaVersion = schemaVersion,
        profileRevision = profileRevision,
        identity = identity.toDomain(),
        frame = frame.toDomain(),
        brakes = brakes.toDomain(),
        drivetrain = drivetrainV2.toDomain(),
        rollingSystem = rollingSystem.toDomain(),
        suspension = suspension.toDomain(),
        cockpit = cockpit.toDomain(),
        seating = seating.toDomain(),
        electricAssist = electricAssist.toDomain(),
        legacy =
            LegacyBikePresentation(
                make, model, modelYear, bikeType, frameMaterial, drivetrain, brakeType, wheelSize, tireSize, notes,
            ),
        createdAt = createdAt,
        updatedAt = updatedAt,
    )

internal fun BikeProfileEdit.toCreateDto() =
    BikeProfileCreateDto(
        displayName = displayName,
        notes = notes,
        identity = identity.toDto(),
        frame = frame.toDto(),
        brakes = brakes.toDto(),
        drivetrainV2 = drivetrain.toDto(),
        rollingSystem = rollingSystem.toDto(),
        suspension = suspension.toDto(),
        cockpit = cockpit.toDto(),
        seating = seating.toDto(),
        electricAssist = electricAssist.toDto(),
    )

internal fun BikeProfileEdit.toPatchDto() =
    BikeProfilePatchDto(
        displayName = displayName,
        notes = notes,
        identity = identity.toDto(),
        frame = frame.toDto(),
        brakes = brakes.toDto(),
        drivetrainV2 = drivetrain.toDto(),
        rollingSystem = rollingSystem.toDto(),
        suspension = suspension.toDto(),
        cockpit = cockpit.toDto(),
        seating = seating.toDto(),
        electricAssist = electricAssist.toDto(),
    )

/**
 * Encode a create request without serializing unspecified optional fields.
 *
 * The server supplies its own defaults for omitted compatibility fields, so a
 * new profile needs only a display name.
 */
internal fun BikeProfileEdit.toCreateRequest(): JsonObject =
    requireNotNull(
        sparseRequestJson.encodeToJsonElement(BikeProfileCreateDto.serializer(), toCreateDto()).pruned() as? JsonObject,
    )

/**
 * Produce a partial update that carries only the user's changes.
 *
 * An explicit JSON null is retained only when a loaded value changed to null,
 * which is the public contract for a manual clear. Unchanged nulls are omitted.
 */
internal fun BikeProfileEdit.toPatchRequest(original: BikeProfileEdit): JsonObject {
    val originalJson = completeRequestJson.encodeToJsonElement(BikeProfilePatchDto.serializer(), original.toPatchDto())
    val updatedJson = completeRequestJson.encodeToJsonElement(BikeProfilePatchDto.serializer(), toPatchDto())
    return requireNotNull(jsonDiff(originalJson, updatedJson) as? JsonObject)
}

private val sparseRequestJson =
    Json {
        encodeDefaults = false
        explicitNulls = false
    }

private val completeRequestJson =
    Json {
        encodeDefaults = true
        explicitNulls = true
    }

private fun JsonElement.pruned(): JsonElement? =
    when (this) {
        is JsonObject ->
            JsonObject(
                entries.mapNotNull { (key, value) -> value.pruned()?.let { key to it } }.toMap(),
            ).takeIf { it.isNotEmpty() }
        else -> this
    }

private fun jsonDiff(
    original: JsonElement,
    updated: JsonElement,
): JsonElement? =
    when {
        original == updated -> null
        original is JsonObject && updated is JsonObject ->
            JsonObject(
                updated.entries
                    .mapNotNull { (key, updatedValue) ->
                        jsonDiff(original[key] ?: updatedValue, updatedValue)?.let { key to it }
                    }.toMap(),
            ).takeIf { it.isNotEmpty() }
        else -> updated
    }

private fun String?.toPresence() =
    when (this) {
        "unknown" -> ComponentPresence.UNKNOWN
        "present" -> ComponentPresence.PRESENT
        "absent" -> ComponentPresence.ABSENT
        else -> null
    }

private fun ComponentIdentityDto.toDomain() = ComponentIdentity(presence.toPresence(), manufacturer, model)

private fun BikeIdentityDto.toDomain() = BikeIdentity(make, model, modelYear, bikeType)

private fun BikeFrameDto.toDomain() = BikeFrame(material, sizeLabel, primaryColor, secondaryColor)

private fun BrakeAssemblyDto.toDomain() =
    BrakeAssembly(
        ComponentIdentity(presence.toPresence(), manufacturer, model),
        mechanism,
        actuation,
        control?.toDomain(),
        brakeUnit?.toDomain(),
        rotor?.toDomain(),
    )

private fun BrakeUnitDto.toDomain(): BrakeUnit {
    return BrakeUnit(ComponentIdentity(presence.toPresence(), manufacturer, model), mountStandard, padFamily)
}

private fun RotorDto.toDomain(): Rotor {
    return Rotor(ComponentIdentity(presence.toPresence(), manufacturer, model), diameterMm)
}

private fun BikeBrakesDto.toDomain() = BikeBrakes(front.toDomain(), rear.toDomain())

private fun DrivetrainRoleDto.toDomain() =
    DrivetrainRole(
        ComponentIdentity(
            presence.toPresence(),
            manufacturer,
            model,
        ),
        actuation = actuation,
        speedCount = speedCount,
        mountType = mountType,
        chainringCount = chainringCount,
        chainringToothCounts = chainringToothCounts,
        clusterType = clusterType,
        smallestSprocketTeeth = smallestSprocketTeeth,
        largestSprocketTeeth = largestSprocketTeeth,
        driverInterface = driverInterface,
        speedCompatibility = speedCompatibility,
        interfaceName = interfaceName,
        shellWidthMm = shellWidthMm,
    )

private fun BikeDrivetrainDto.toDomain() =
    BikeDrivetrain(
        architecture = architecture,
        driveMedium = driveMedium,
        frontChainringCount = frontChainringCount,
        rearSpeedCount = rearSpeedCount,
        frontShifter = frontShifter?.toDomain(),
        rearShifter = rearShifter?.toDomain(),
        frontDerailleur = frontDerailleur?.toDomain(),
        rearDerailleur = rearDerailleur?.toDomain(),
        crankset = crankset?.toDomain(),
        rearCluster = rearCluster?.toDomain(),
        chain = chain?.toDomain(),
        belt = belt?.toDomain(),
        gearUnit = gearUnit?.toDomain(),
        bottomBracket = bottomBracket?.toDomain(),
    )

private fun WheelComponentDto.toDomain() =
    WheelComponent(
        ComponentIdentity(presence.toPresence(), manufacturer, model),
        nominalSize,
        isoBsdMm,
    )

private fun RimComponentDto.toDomain(): RimComponent {
    return RimComponent(ComponentIdentity(presence.toPresence(), manufacturer, model), internalWidthMm)
}

private fun TireComponentDto.toDomain(): TireComponent {
    return TireComponent(
        ComponentIdentity(presence.toPresence(), manufacturer, model),
        markedSize,
        isoWidthMm,
        isoBsdMm,
        setup,
        tubelessReady,
    )
}

private fun HubComponentDto.toDomain() =
    HubComponent(
        ComponentIdentity(presence.toPresence(), manufacturer, model),
        axleType,
        axleStandard,
        rotorMount,
        driverInterface,
    )

private fun WheelPositionDto.toDomain(): WheelPosition {
    return WheelPosition(wheel?.toDomain(), rim?.toDomain(), tire?.toDomain(), hub?.toDomain())
}

private fun BikeRollingSystemDto.toDomain(): BikeRollingSystem {
    return BikeRollingSystem(front.toDomain(), rear.toDomain())
}

private fun ForkDto.toDomain() = Fork(type, manufacturer, model, travelMm)

private fun BikeSuspensionDto.toDomain() = BikeSuspension(fork?.toDomain(), rearShock?.toDomain(), rearTravelMm)

private fun HandlebarDto.toDomain() = Handlebar(style, manufacturer, model)

private fun StemDto.toDomain(): Stem {
    return Stem(type, manufacturer, model)
}

private fun HeadsetDto.toDomain() = Headset(type)

private fun BikeCockpitDto.toDomain() = BikeCockpit(handlebar?.toDomain(), stem?.toDomain(), headset?.toDomain())

private fun SeatpostDto.toDomain(): Seatpost {
    return Seatpost(ComponentIdentity(presence.toPresence(), manufacturer, model), type, diameterMm)
}

private fun BikeSeatingDto.toDomain() = BikeSeating(seatpost?.toDomain())

private fun ElectricMotorDto.toDomain() = ElectricMotor(position, manufacturer, model)

private fun ElectricBatteryDto.toDomain(): ElectricBattery {
    return ElectricBattery(manufacturer, model, nominalVoltageV)
}

private fun BikeElectricAssistDto.toDomain() =
    BikeElectricAssist(
        presence.toPresence(),
        systemManufacturer,
        systemModel,
        motor?.toDomain(),
        battery?.toDomain(),
    )

private fun ComponentPresence?.toDto() =
    when (this) {
        ComponentPresence.UNKNOWN -> "unknown"
        ComponentPresence.PRESENT -> "present"
        ComponentPresence.ABSENT -> "absent"
        null -> null
    }

private fun ComponentIdentity.toDto() = ComponentIdentityDto(presence.toDto(), manufacturer, model)

private fun BikeIdentity.toDto() = BikeIdentityDto(make, model, modelYear, bikeType)

private fun BikeFrame.toDto() = BikeFrameDto(material, sizeLabel, primaryColor, secondaryColor)

private fun BrakeAssembly.toDto() =
    BrakeAssemblyDto(
        presence = component.presence.toDto(),
        manufacturer = component.manufacturer,
        model = component.model,
        mechanism = mechanism,
        actuation = actuation,
        control = control?.toDto(),
        brakeUnit = brakeUnit?.toDto(),
        rotor = rotor?.toDto(),
    )

private fun BrakeUnit.toDto() = BrakeUnitDto(component.presence.toDto(), component.manufacturer, component.model, mountStandard, padFamily)

private fun Rotor.toDto() = RotorDto(component.presence.toDto(), component.manufacturer, component.model, diameterMm)

private fun BikeBrakes.toDto() = BikeBrakesDto(front.toDto(), rear.toDto())

private fun DrivetrainRole.toDto() =
    DrivetrainRoleDto(
        presence = component.presence.toDto(), manufacturer = component.manufacturer, model = component.model,
        actuation = actuation, speedCount = speedCount, mountType = mountType,
        chainringCount = chainringCount, chainringToothCounts = chainringToothCounts,
        clusterType = clusterType, smallestSprocketTeeth = smallestSprocketTeeth,
        largestSprocketTeeth = largestSprocketTeeth, driverInterface = driverInterface,
        speedCompatibility = speedCompatibility, interfaceName = interfaceName, shellWidthMm = shellWidthMm,
    )

private fun BikeDrivetrain.toDto() =
    BikeDrivetrainDto(
        architecture = architecture, driveMedium = driveMedium, frontShifter = frontShifter?.toDto(),
        rearShifter = rearShifter?.toDto(), frontDerailleur = frontDerailleur?.toDto(),
        rearDerailleur = rearDerailleur?.toDto(), crankset = crankset?.toDto(), rearCluster = rearCluster?.toDto(),
        chain = chain?.toDto(), belt = belt?.toDto(), gearUnit = gearUnit?.toDto(), bottomBracket = bottomBracket?.toDto(),
    )

private fun WheelComponent.toDto() =
    WheelComponentDto(component.presence.toDto(), component.manufacturer, component.model, nominalSize, isoBsdMm)

private fun RimComponent.toDto() = RimComponentDto(component.presence.toDto(), component.manufacturer, component.model, internalWidthMm)

private fun TireComponent.toDto() =
    TireComponentDto(
        component.presence.toDto(),
        component.manufacturer,
        component.model,
        markedSize,
        isoWidthMm,
        isoBsdMm,
        setup,
        tubelessReady,
    )

private fun HubComponent.toDto() =
    HubComponentDto(
        component.presence.toDto(),
        component.manufacturer,
        component.model,
        axleType,
        axleStandard,
        rotorMount,
        driverInterface,
    )

private fun WheelPosition.toDto() = WheelPositionDto(wheel?.toDto(), rim?.toDto(), tire?.toDto(), hub?.toDto())

private fun BikeRollingSystem.toDto() = BikeRollingSystemDto(front.toDto(), rear.toDto())

private fun Fork.toDto() = ForkDto(type, manufacturer, model, travelMm)

private fun BikeSuspension.toDto() = BikeSuspensionDto(fork?.toDto(), rearShock?.toDto(), rearTravelMm)

private fun Handlebar.toDto() = HandlebarDto(style, manufacturer, model)

private fun Stem.toDto() = StemDto(type, manufacturer, model)

private fun Headset.toDto() = HeadsetDto(type)

private fun BikeCockpit.toDto() = BikeCockpitDto(handlebar?.toDto(), stem?.toDto(), headset?.toDto())

private fun Seatpost.toDto() = SeatpostDto(component.presence.toDto(), component.manufacturer, component.model, type, diameterMm)

private fun BikeSeating.toDto() = BikeSeatingDto(seatpost?.toDto())

private fun ElectricMotor.toDto() = ElectricMotorDto(position, manufacturer, model)

private fun ElectricBattery.toDto() = ElectricBatteryDto(manufacturer, model, nominalVoltageV)

private fun BikeElectricAssist.toDto() =
    BikeElectricAssistDto(presence.toDto(), systemManufacturer, systemModel, motor?.toDto(), battery?.toDto())
