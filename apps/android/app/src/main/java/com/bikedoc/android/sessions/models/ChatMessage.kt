package com.bikedoc.android.sessions.models

import java.time.Instant

data class ChatMessage(
    val id: String,
    val role: Role,
    val text: String,
    val artifactIds: List<String> = emptyList(),
    val isStreaming: Boolean = false,
    val deliveryState: DeliveryState = DeliveryState.Sent,
    val respondsToInputRequestId: String? = null,
    val createdAt: Instant,
)

enum class Role {
    User,
    Assistant,
    System,
}

enum class DeliveryState {
    Sending,
    Sent,
    Failed,
}
