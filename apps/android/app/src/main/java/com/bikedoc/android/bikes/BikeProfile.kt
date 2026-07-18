package com.bikedoc.android.bikes

/** Current public bike_profile.v2 projection, independent of Retrofit transport types. */
data class BikeProfile(
    val id: String,
    val userId: String,
    val displayName: String,
    val hasRepairSessions: Boolean,
    val schemaVersion: String,
    val profileRevision: Long,
    val identity: BikeIdentity,
    val frame: BikeFrame,
    val brakes: BikeBrakes,
    val drivetrain: BikeDrivetrain,
    val rollingSystem: BikeRollingSystem,
    val suspension: BikeSuspension,
    val cockpit: BikeCockpit,
    val seating: BikeSeating,
    val electricAssist: BikeElectricAssist,
    val legacy: LegacyBikePresentation,
    val createdAt: String,
    val updatedAt: String,
)

data class LegacyBikePresentation(
    val make: String? = null,
    val model: String? = null,
    val modelYear: Int? = null,
    val bikeType: String,
    val frameMaterial: String? = null,
    val drivetrain: String? = null,
    val brakeType: String? = null,
    val wheelSize: String? = null,
    val tireSize: String? = null,
    val notes: String? = null,
)

enum class ComponentPresence { UNKNOWN, PRESENT, ABSENT }

data class ComponentIdentity(
    val presence: ComponentPresence? = null,
    val manufacturer: String? = null,
    val model: String? = null,
)

data class BikeIdentity(
    val make: String? = null,
    val model: String? = null,
    val modelYear: Int? = null,
    val bikeType: String? = null,
)

data class BikeFrame(
    val material: String? = null,
    val sizeLabel: String? = null,
    val primaryColor: String? = null,
    val secondaryColor: String? = null,
)

data class BrakeAssembly(
    val component: ComponentIdentity = ComponentIdentity(),
    val mechanism: String? = null,
    val actuation: String? = null,
    val control: ComponentIdentity? = null,
    val brakeUnit: BrakeUnit? = null,
    val rotor: Rotor? = null,
)

data class BrakeUnit(
    val component: ComponentIdentity = ComponentIdentity(),
    val mountStandard: String? = null,
    val padFamily: String? = null,
)

data class Rotor(
    val component: ComponentIdentity = ComponentIdentity(),
    val diameterMm: Double? = null,
)

data class BikeBrakes(val front: BrakeAssembly, val rear: BrakeAssembly)

data class DrivetrainRole(
    val component: ComponentIdentity = ComponentIdentity(),
    val actuation: String? = null,
    val speedCount: Int? = null,
    val mountType: String? = null,
    val chainringCount: Int? = null,
    val chainringToothCounts: String? = null,
    val clusterType: String? = null,
    val smallestSprocketTeeth: Int? = null,
    val largestSprocketTeeth: Int? = null,
    val driverInterface: String? = null,
    val speedCompatibility: Int? = null,
    val interfaceName: String? = null,
    val shellWidthMm: Double? = null,
)

data class BikeDrivetrain(
    val architecture: String? = null,
    val driveMedium: String? = null,
    val frontChainringCount: Int? = null,
    val rearSpeedCount: Int? = null,
    val frontShifter: DrivetrainRole? = null,
    val rearShifter: DrivetrainRole? = null,
    val frontDerailleur: DrivetrainRole? = null,
    val rearDerailleur: DrivetrainRole? = null,
    val crankset: DrivetrainRole? = null,
    val rearCluster: DrivetrainRole? = null,
    val chain: DrivetrainRole? = null,
    val belt: DrivetrainRole? = null,
    val gearUnit: DrivetrainRole? = null,
    val bottomBracket: DrivetrainRole? = null,
)

data class WheelComponent(
    val component: ComponentIdentity = ComponentIdentity(),
    val nominalSize: String? = null,
    val isoBsdMm: Int? = null,
)

data class RimComponent(
    val component: ComponentIdentity = ComponentIdentity(),
    val internalWidthMm: Double? = null,
)

data class TireComponent(
    val component: ComponentIdentity = ComponentIdentity(),
    val markedSize: String? = null,
    val isoWidthMm: Int? = null,
    val isoBsdMm: Int? = null,
    val setup: String? = null,
    val tubelessReady: Boolean? = null,
)

data class HubComponent(
    val component: ComponentIdentity = ComponentIdentity(),
    val axleType: String? = null,
    val axleStandard: String? = null,
    val rotorMount: String? = null,
    val driverInterface: String? = null,
)

data class WheelPosition(
    val wheel: WheelComponent? = null,
    val rim: RimComponent? = null,
    val tire: TireComponent? = null,
    val hub: HubComponent? = null,
)

data class BikeRollingSystem(val front: WheelPosition, val rear: WheelPosition)

data class Fork(
    val type: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    val travelMm: Int? = null,
)

data class BikeSuspension(
    val fork: Fork? = null,
    val rearShock: ComponentIdentity? = null,
    val rearTravelMm: Int? = null,
)

data class Handlebar(val style: String? = null, val manufacturer: String? = null, val model: String? = null)

data class Stem(val type: String? = null, val manufacturer: String? = null, val model: String? = null)

data class Headset(val type: String? = null)

data class BikeCockpit(
    val handlebar: Handlebar? = null,
    val stem: Stem? = null,
    val headset: Headset? = null,
)

data class Seatpost(
    val component: ComponentIdentity = ComponentIdentity(),
    val type: String? = null,
    val diameterMm: Double? = null,
)

data class BikeSeating(val seatpost: Seatpost? = null)

data class ElectricMotor(val position: String? = null, val manufacturer: String? = null, val model: String? = null)

data class ElectricBattery(
    val manufacturer: String? = null,
    val model: String? = null,
    val nominalVoltageV: Double? = null,
)

data class BikeElectricAssist(
    val presence: ComponentPresence? = null,
    val systemManufacturer: String? = null,
    val systemModel: String? = null,
    val motor: ElectricMotor? = null,
    val battery: ElectricBattery? = null,
)

data class BikeProfileEdit(
    val displayName: String,
    val identity: BikeIdentity = BikeIdentity(),
    val frame: BikeFrame = BikeFrame(),
    val brakes: BikeBrakes = BikeBrakes(BrakeAssembly(), BrakeAssembly()),
    val drivetrain: BikeDrivetrain = BikeDrivetrain(),
    val rollingSystem: BikeRollingSystem = BikeRollingSystem(WheelPosition(), WheelPosition()),
    val suspension: BikeSuspension = BikeSuspension(),
    val cockpit: BikeCockpit = BikeCockpit(),
    val seating: BikeSeating = BikeSeating(),
    val electricAssist: BikeElectricAssist = BikeElectricAssist(),
    val notes: String? = null,
)
