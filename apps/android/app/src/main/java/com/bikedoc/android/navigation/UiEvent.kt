package com.bikedoc.android.navigation

sealed class UiEvent {
    data class ShowSnackbar(val message: String) : UiEvent()

    data class NavigateTo(val route: String) : UiEvent()

    data class NavigateBackWithResult(
        val key: String,
        val value: Boolean,
    ) : UiEvent()

    data object NavigateBack : UiEvent()
}
