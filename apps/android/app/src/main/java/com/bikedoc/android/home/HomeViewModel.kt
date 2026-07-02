package com.bikedoc.android.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.api.ApiResult
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
        private val homeRepository: HomeRepository,
    ) : ViewModel() {
        private val _uiState = MutableStateFlow(HomeUiState())
        val uiState: StateFlow<HomeUiState> = _uiState.asStateFlow()

        private val eventChannel = Channel<UiEvent>(Channel.BUFFERED)
        val events = eventChannel.receiveAsFlow()

        init {
            refresh()
        }

        fun openBikes(selectionMode: Boolean) {
            viewModelScope.launch {
                eventChannel.send(UiEvent.NavigateTo(AppRoute.Bikes.create(selectionMode)))
            }
        }

        fun refresh() {
            viewModelScope.launch {
                if (!authProvider.isSignedIn()) {
                    redirectToAuth()
                    return@launch
                }

                _uiState.value = _uiState.value.copy(isLoading = true, error = null)
                when (val result = homeRepository.getCurrentUser()) {
                    is ApiResult.Success -> {
                        _uiState.value =
                            HomeUiState(
                                displayName = result.data.displayName,
                                isLoading = false,
                                error = null,
                            )
                    }
                    is ApiResult.Error -> {
                        if (result.code == 401) {
                            authProvider.signOut()
                            redirectToAuth()
                        } else {
                            _uiState.value =
                                _uiState.value.copy(
                                    isLoading = false,
                                    error = result.message,
                                )
                        }
                    }
                    ApiResult.Loading -> {
                        _uiState.value = _uiState.value.copy(isLoading = true)
                    }
                }
            }
        }

        fun signOut() {
            authProvider.signOut()
            viewModelScope.launch { redirectToAuth() }
        }

        private suspend fun redirectToAuth() {
            eventChannel.send(UiEvent.NavigateTo(AppRoute.Auth.route))
        }
    }
