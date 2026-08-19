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
    val bikes: List<HomeBike> = emptyList(),
    val selectedBikeId: String? = null,
    val isLoading: Boolean = false,
    val isStartingRepair: Boolean = false,
    val isSetupExpanded: Boolean = false,
    val startingDetail: String = "",
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

        fun openResumeRepair() {
            viewModelScope.launch {
                eventChannel.send(UiEvent.NavigateTo(AppRoute.Bikes.create(selectionMode = true, resumeOnly = true)))
            }
        }

        fun openSelectedBikeProfile() {
            val bikeId = _uiState.value.selectedBikeId ?: return
            viewModelScope.launch {
                eventChannel.send(UiEvent.NavigateTo(AppRoute.BikeEdit.create(bikeId)))
            }
        }

        fun startSetup() {
            _uiState.value = _uiState.value.copy(isSetupExpanded = true, error = null)
        }

        fun closeSetup() {
            _uiState.value = _uiState.value.copy(isSetupExpanded = false)
        }

        fun onStartingDetailChanged(value: String) {
            _uiState.value = _uiState.value.copy(startingDetail = value)
        }

        fun selectRepairBike(bikeId: String?) {
            _uiState.value = _uiState.value.copy(selectedBikeId = bikeId)
        }

        fun startRepair() {
            if (_uiState.value.isStartingRepair) return

            viewModelScope.launch {
                val startingDetail = _uiState.value.startingDetail.trim()
                _uiState.value = _uiState.value.copy(isStartingRepair = true, error = null)
                when (val result = homeRepository.startRepair(_uiState.value.selectedBikeId)) {
                    is ApiResult.Success -> {
                        _uiState.value = _uiState.value.copy(isStartingRepair = false)
                        eventChannel.send(
                            UiEvent.NavigateTo(
                                AppRoute.DiagnosticChat.create(
                                    sessionId = result.data.id,
                                    startingDetail = startingDetail,
                                ),
                            ),
                        )
                    }
                    is ApiResult.Error -> {
                        _uiState.value = _uiState.value.copy(isStartingRepair = false, error = result.message)
                    }
                    ApiResult.Loading -> _uiState.value = _uiState.value.copy(isStartingRepair = true)
                }
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
                        loadBikes(displayName = result.data.displayName)
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

        private suspend fun loadBikes(displayName: String?) {
            when (val result = homeRepository.getBikes()) {
                is ApiResult.Success ->
                    _uiState.value =
                        HomeUiState(
                            displayName = displayName,
                            bikes = result.data,
                            selectedBikeId = result.data.firstOrNull()?.id,
                            isLoading = false,
                        )
                is ApiResult.Error ->
                    _uiState.value =
                        HomeUiState(
                            displayName = displayName,
                            isLoading = false,
                            error = result.message,
                        )
                ApiResult.Loading -> _uiState.value = _uiState.value.copy(isLoading = true)
            }
        }
    }
