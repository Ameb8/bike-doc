package com.bikedoc.android.api.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class Bike(
    val id: String,
    @SerialName("user_id")
    val userId: String,
    @SerialName("display_name")
    val displayName: String,
    @SerialName("has_repair_sessions")
    val hasRepairSessions: Boolean,
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
data class BikeListResponse(
    val items: List<Bike>,
    @SerialName("next_cursor")
    val nextCursor: String? = null,
)

@Serializable
data class BikeCreate(
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
)

@Serializable
data class BikePatch(
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
)
