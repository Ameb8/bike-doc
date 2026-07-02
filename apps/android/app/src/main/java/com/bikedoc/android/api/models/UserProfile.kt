package com.bikedoc.android.api.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class UserProfile(
    val id: String,
    val email: String,
    @SerialName("display_name")
    val displayName: String,
    @SerialName("skill_level")
    val skillLevel: String,
    @SerialName("created_at")
    val createdAt: String,
)
