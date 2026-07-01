package com.bikedoc.android.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.auth.AuthProvider
import com.bikedoc.android.navigation.AppRoute
import com.bikedoc.android.navigation.UiEvent
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class HomeUiState(
    val displayName: String? = null,
    val isLoading: Boolean = false,
    val error: String? = null,
)

@HiltViewModel
class HomeViewModel
    @Inject
    constructor(
        private val authProvider: AuthProvider,
    ) : ViewModel() {
        private val _uiState = MutableStateFlow(HomeUiState())
        val uiState: StateFlow<HomeUiState> = _uiState.asStateFlow()

        private val eventChannel = Channel<UiEvent>(Channel.BUFFERED)
        val events = eventChannel.receiveAsFlow()

        fun openBikes(selectionMode: Boolean) {
            viewModelScope.launch {
                eventChannel.send(UiEvent.NavigateTo(AppRoute.Bikes.create(selectionMode)))
            }
        }

        fun signOut() {
            authProvider.signOut()
            viewModelScope.launch {
                eventChannel.send(UiEvent.NavigateTo(AppRoute.Auth.route))
            }
        }
    }
