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
        displayName, make, model, modelYear, bikeType, frameMaterial, drivetrain, brakeType, wheelSize, tireSize, notes,
    )

internal fun BikeProfileEdit.toPatchDto() =
    BikeProfilePatchDto(
        displayName, make, model, modelYear, bikeType, frameMaterial, drivetrain, brakeType, wheelSize, tireSize, notes,
    )

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
