package com.bikedoc.android.navigation

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

    data object DiagnosticChat : AppRoute("sessions/{sessionId}/chat") {
        fun create(sessionId: String): String = "sessions/$sessionId/chat"
    }

    data object DiagnosticReport : AppRoute("sessions/{sessionId}/reports/{reportId}") {
        fun create(
            sessionId: String,
            reportId: String,
        ): String = "sessions/$sessionId/reports/$reportId"
    }
}
