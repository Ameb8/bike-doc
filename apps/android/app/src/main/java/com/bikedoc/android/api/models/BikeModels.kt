package com.bikedoc.android.api.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class BikeProfileDto(
    val id: String,
    @SerialName("user_id")
    val userId: String,
    @SerialName("display_name")
    val displayName: String,
    @SerialName("has_repair_sessions")
    val hasRepairSessions: Boolean,
    @SerialName("schema_version")
    val schemaVersion: String,
    @SerialName("profile_revision")
    val profileRevision: Long,
    val identity: BikeIdentityDto,
    val frame: BikeFrameDto,
    val brakes: BikeBrakesDto,
    @SerialName("drivetrain_v2")
    val drivetrainV2: BikeDrivetrainDto,
    @SerialName("rolling_system")
    val rollingSystem: BikeRollingSystemDto,
    val suspension: BikeSuspensionDto,
    val cockpit: BikeCockpitDto,
    val seating: BikeSeatingDto,
    @SerialName("electric_assist")
    val electricAssist: BikeElectricAssistDto,
    val make: String? = null,
    val model: String? = null,
    @SerialName("model_year")
    val modelYear: Int? = null,
    @SerialName("bike_type")
    val bikeType: String,
    @SerialName("frame_material")
    val frameMaterial: String? = null,
    val drivetrain: String? = null,
    @SerialName("brake_type")
    val brakeType: String? = null,
    @SerialName("wheel_size")
    val wheelSize: String? = null,
    @SerialName("tire_size")
    val tireSize: String? = null,
    val notes: String? = null,
    @SerialName("created_at")
    val createdAt: String,
    @SerialName("updated_at")
    val updatedAt: String,
)

@Serializable
data class BikeListResponseDto(
    val items: List<BikeProfileDto>,
    @SerialName("next_cursor")
    val nextCursor: String? = null,
)

@Serializable
data class BikeProfileCreateDto(
    @SerialName("display_name")
    val displayName: String,
    val make: String? = null,
    val model: String? = null,
    @SerialName("model_year")
    val modelYear: Int? = null,
    @SerialName("bike_type")
    val bikeType: String = "unknown",
    @SerialName("frame_material")
    val frameMaterial: String? = null,
    val drivetrain: String? = null,
    @SerialName("brake_type")
    val brakeType: String? = null,
    @SerialName("wheel_size")
    val wheelSize: String? = null,
    @SerialName("tire_size")
    val tireSize: String? = null,
    val notes: String? = null,
    val identity: BikeIdentityDto? = null,
    val frame: BikeFrameDto? = null,
    val brakes: BikeBrakesDto? = null,
    @SerialName("drivetrain_v2") val drivetrainV2: BikeDrivetrainDto? = null,
    @SerialName("rolling_system") val rollingSystem: BikeRollingSystemDto? = null,
    val suspension: BikeSuspensionDto? = null,
    val cockpit: BikeCockpitDto? = null,
    val seating: BikeSeatingDto? = null,
    @SerialName("electric_assist") val electricAssist: BikeElectricAssistDto? = null,
)

@Serializable
data class BikeProfilePatchDto(
    @SerialName("display_name")
    val displayName: String? = null,
    val make: String? = null,
    val model: String? = null,
    @SerialName("model_year")
    val modelYear: Int? = null,
    @SerialName("bike_type")
    val bikeType: String? = null,
    @SerialName("frame_material")
    val frameMaterial: String? = null,
    val drivetrain: String? = null,
    @SerialName("brake_type")
    val brakeType: String? = null,
    @SerialName("wheel_size")
    val wheelSize: String? = null,
    @SerialName("tire_size")
    val tireSize: String? = null,
    val notes: String? = null,
    val identity: BikeIdentityDto? = null,
    val frame: BikeFrameDto? = null,
    val brakes: BikeBrakesDto? = null,
    @SerialName("drivetrain_v2") val drivetrainV2: BikeDrivetrainDto? = null,
    @SerialName("rolling_system") val rollingSystem: BikeRollingSystemDto? = null,
    val suspension: BikeSuspensionDto? = null,
    val cockpit: BikeCockpitDto? = null,
    val seating: BikeSeatingDto? = null,
    @SerialName("electric_assist") val electricAssist: BikeElectricAssistDto? = null,
)

@Serializable
data class ComponentIdentityDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
)

@Serializable
data class BikeIdentityDto(
    val make: String? = null,
    val model: String? = null,
    @SerialName("model_year") val modelYear: Int? = null,
    @SerialName("bike_type") val bikeType: String? = null,
)

@Serializable
data class BikeFrameDto(
    val material: String? = null,
    @SerialName("size_label") val sizeLabel: String? = null,
    @SerialName("primary_color") val primaryColor: String? = null,
    @SerialName("secondary_color") val secondaryColor: String? = null,
)

@Serializable
data class BrakeAssemblyDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    val mechanism: String? = null,
    val actuation: String? = null,
    val control: ComponentIdentityDto? = null,
    @SerialName("brake_unit") val brakeUnit: BrakeUnitDto? = null,
    val rotor: RotorDto? = null,
)

@Serializable
data class BrakeUnitDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    @SerialName("mount_standard") val mountStandard: String? = null,
    @SerialName("pad_family") val padFamily: String? = null,
)

@Serializable
data class RotorDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    @SerialName("diameter_mm") val diameterMm: Double? = null,
)

@Serializable
data class BikeBrakesDto(
    val front: BrakeAssemblyDto = BrakeAssemblyDto(),
    val rear: BrakeAssemblyDto = BrakeAssemblyDto(),
)

@Serializable
data class DrivetrainRoleDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    val actuation: String? = null,
    @SerialName("speed_count") val speedCount: Int? = null,
    @SerialName("mount_type") val mountType: String? = null,
    @SerialName("chainring_count") val chainringCount: Int? = null,
    @SerialName("chainring_tooth_counts") val chainringToothCounts: String? = null,
    @SerialName("cluster_type") val clusterType: String? = null,
    @SerialName("smallest_sprocket_teeth") val smallestSprocketTeeth: Int? = null,
    @SerialName("largest_sprocket_teeth") val largestSprocketTeeth: Int? = null,
    @SerialName("driver_interface") val driverInterface: String? = null,
    @SerialName("speed_compatibility") val speedCompatibility: Int? = null,
    @SerialName("interface") val interfaceName: String? = null,
    @SerialName("shell_width_mm") val shellWidthMm: Double? = null,
)

@Serializable
data class BikeDrivetrainDto(
    val architecture: String? = null,
    @SerialName("drive_medium") val driveMedium: String? = null,
    @SerialName("front_chainring_count") val frontChainringCount: Int? = null,
    @SerialName("rear_speed_count") val rearSpeedCount: Int? = null,
    @SerialName("front_shifter") val frontShifter: DrivetrainRoleDto? = null,
    @SerialName("rear_shifter") val rearShifter: DrivetrainRoleDto? = null,
    @SerialName("front_derailleur") val frontDerailleur: DrivetrainRoleDto? = null,
    @SerialName("rear_derailleur") val rearDerailleur: DrivetrainRoleDto? = null,
    val crankset: DrivetrainRoleDto? = null,
    @SerialName("rear_cluster") val rearCluster: DrivetrainRoleDto? = null,
    val chain: DrivetrainRoleDto? = null,
    val belt: DrivetrainRoleDto? = null,
    @SerialName("gear_unit") val gearUnit: DrivetrainRoleDto? = null,
    @SerialName("bottom_bracket") val bottomBracket: DrivetrainRoleDto? = null,
)

@Serializable
data class WheelComponentDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    @SerialName("nominal_size") val nominalSize: String? = null,
    @SerialName("iso_bsd_mm") val isoBsdMm: Int? = null,
)

@Serializable
data class RimComponentDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    @SerialName("internal_width_mm") val internalWidthMm: Double? = null,
)

@Serializable
data class TireComponentDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    @SerialName("marked_size") val markedSize: String? = null,
    @SerialName("iso_width_mm") val isoWidthMm: Int? = null,
    @SerialName("iso_bsd_mm") val isoBsdMm: Int? = null,
    val setup: String? = null,
    @SerialName("tubeless_ready") val tubelessReady: Boolean? = null,
)

@Serializable
data class HubComponentDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    @SerialName("axle_type") val axleType: String? = null,
    @SerialName("axle_standard") val axleStandard: String? = null,
    @SerialName("rotor_mount") val rotorMount: String? = null,
    @SerialName("driver_interface") val driverInterface: String? = null,
)

@Serializable
data class WheelPositionDto(
    val wheel: WheelComponentDto? = null,
    val rim: RimComponentDto? = null,
    val tire: TireComponentDto? = null,
    val hub: HubComponentDto? = null,
)

@Serializable
data class BikeRollingSystemDto(
    val front: WheelPositionDto = WheelPositionDto(),
    val rear: WheelPositionDto = WheelPositionDto(),
)

@Serializable
data class ForkDto(
    val type: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    @SerialName("travel_mm") val travelMm: Int? = null,
)

@Serializable
data class BikeSuspensionDto(
    val fork: ForkDto? = null,
    @SerialName("rear_shock") val rearShock: ComponentIdentityDto? = null,
    @SerialName("rear_travel_mm") val rearTravelMm: Int? = null,
)

@Serializable
data class HandlebarDto(val style: String? = null, val manufacturer: String? = null, val model: String? = null)

@Serializable
data class StemDto(val type: String? = null, val manufacturer: String? = null, val model: String? = null)

@Serializable
data class HeadsetDto(val type: String? = null)

@Serializable
data class BikeCockpitDto(
    val handlebar: HandlebarDto? = null,
    val stem: StemDto? = null,
    val headset: HeadsetDto? = null,
)

@Serializable
data class SeatpostDto(
    val presence: String? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    val type: String? = null,
    @SerialName("diameter_mm") val diameterMm: Double? = null,
)

@Serializable
data class BikeSeatingDto(val seatpost: SeatpostDto? = null)

@Serializable
data class ElectricMotorDto(val position: String? = null, val manufacturer: String? = null, val model: String? = null)

@Serializable
data class ElectricBatteryDto(
    val manufacturer: String? = null,
    val model: String? = null,
    @SerialName("nominal_voltage_v") val nominalVoltageV: Double? = null,
)

@Serializable
data class BikeElectricAssistDto(
    val presence: String? = null,
    @SerialName("system_manufacturer") val systemManufacturer: String? = null,
    @SerialName("system_model") val systemModel: String? = null,
    val motor: ElectricMotorDto? = null,
    val battery: ElectricBatteryDto? = null,
)
