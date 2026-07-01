package com.bikedoc.android.api.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class PhaseReportList(
    val items: List<PhaseReportEnvelope>,
    @SerialName("next_cursor")
    val nextCursor: String? = null,
)

@Serializable
data class PhaseReportEnvelope(
    val id: String,
    @SerialName("repair_session_id")
    val repairSessionId: String,
    val phase: String,
    val payload: JsonElement,
    @SerialName("created_at")
    val createdAt: String,
)
