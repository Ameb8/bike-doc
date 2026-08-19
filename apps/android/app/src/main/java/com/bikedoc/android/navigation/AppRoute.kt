package com.bikedoc.android.navigation

import android.net.Uri

sealed class AppRoute(val route: String) {
    data object Auth : AppRoute("auth")

    data object Home : AppRoute("home")

    data object Bikes : AppRoute("bikes?selectionMode={selectionMode}&resumeOnly={resumeOnly}") {
        fun create(
            selectionMode: Boolean,
            resumeOnly: Boolean = false,
        ): String = "bikes?selectionMode=$selectionMode&resumeOnly=$resumeOnly"
    }

    data object BikeNew : AppRoute("bikes/new")

    data object BikeEdit : AppRoute("bikes/{bikeId}/edit") {
        fun create(bikeId: String): String = "bikes/$bikeId/edit"
    }

    data object DiagnosticChat : AppRoute("sessions/{sessionId}/chat?startingDetail={startingDetail}") {
        fun create(
            sessionId: String,
            startingDetail: String? = null,
        ): String =
            if (startingDetail.isNullOrBlank()) {
                "sessions/$sessionId/chat"
            } else {
                "sessions/$sessionId/chat?startingDetail=${Uri.encode(startingDetail)}"
            }
    }

    data object DiagnosticReport : AppRoute("sessions/{sessionId}/reports/{reportId}") {
        fun create(
            sessionId: String,
            reportId: String,
        ): String = "sessions/$sessionId/reports/$reportId"
    }
}
