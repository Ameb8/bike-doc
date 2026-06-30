package com.bikedoc.android.navigation

sealed class UiEvent {
    data class ShowSnackbar(val message: String) : UiEvent()

    data class NavigateTo(val route: String) : UiEvent()

    data object NavigateBack : UiEvent()
}
