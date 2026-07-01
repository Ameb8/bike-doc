package com.bikedoc.android.navigation

sealed class AppRoute(val route: String) {
    data object Auth : AppRoute("auth")

    data object Home : AppRoute("home")

    data object Bikes : AppRoute("bikes?selectionMode={selectionMode}") {
        fun create(selectionMode: Boolean): String = "bikes?selectionMode=$selectionMode"
    }

    data object BikeNew : AppRoute("bikes/new")

    data object BikeEdit : AppRoute("bikes/{bikeId}/edit") {
        fun create(bikeId: String): String = "bikes/$bikeId/edit"
    }

    data object DiagnosticChat : AppRoute("sessions/{sessionId}/chat") {
        fun create(sessionId: String): String = "sessions/$sessionId/chat"
    }
}
