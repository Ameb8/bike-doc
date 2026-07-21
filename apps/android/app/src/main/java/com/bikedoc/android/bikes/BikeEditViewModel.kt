@file:Suppress("MaxLineLength")

package com.bikedoc.android.bikes

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.BikeRepository
import com.bikedoc.android.navigation.UiEvent
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class BikeEditUiState(
    val isNew: Boolean = true,
    val profile: BikeProfileEdit = BikeProfileEdit(displayName = ""),
    val originalProfile: BikeProfileEdit? = null,
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val error: String? = null,
    val validationErrors: Map<String, String> = emptyMap(),
)

@HiltViewModel
class BikeEditViewModel
    @Inject
    constructor(
        private val repository: BikeRepository,
        private val savedStateHandle: SavedStateHandle,
    ) : ViewModel() {
        private val bikeId: String? = savedStateHandle["bikeId"]
        private val hasRestoredDraft = FORM_KEYS.any(savedStateHandle::contains)

        private val _uiState =
            MutableStateFlow(
                BikeEditUiState(
                    isNew = bikeId == null,
                    profile = BikeProfileEdit(displayName = savedStateHandle["displayName"] ?: "", notes = savedStateHandle["notes"]),
                    isLoading = bikeId != null,
                ),
            )
        val uiState: StateFlow<BikeEditUiState> = _uiState.asStateFlow()

        private val eventChannel = Channel<UiEvent>(Channel.BUFFERED)
        val events = eventChannel.receiveAsFlow()

        init {
            if (bikeId != null) {
                loadBike(bikeId)
            }
        }

        fun onProfileChanged(profile: BikeProfileEdit) {
            savedStateHandle["displayName"] = profile.displayName
            savedStateHandle["notes"] = profile.notes
            _uiState.value = _uiState.value.copy(profile = profile, error = null)
        }

        fun retry() {
            bikeId?.let(::loadBike)
        }

        fun save() {
            val validationErrors = validate(_uiState.value)
            if (validationErrors.isNotEmpty()) {
                _uiState.value = _uiState.value.copy(validationErrors = validationErrors, error = null)
                return
            }

            viewModelScope.launch {
                _uiState.value =
                    _uiState.value.copy(
                        isSaving = true,
                        error = null,
                        validationErrors = emptyMap(),
                    )

                val result =
                    if (bikeId == null) {
                        repository.createBike(_uiState.value.profile.normalized())
                    } else {
                        repository.updateBike(
                            bikeId,
                            _uiState.value.profile.normalized(),
                            requireNotNull(_uiState.value.originalProfile),
                        )
                    }

                when (result) {
                    is ApiResult.Success -> {
                        clearDraft()
                        _uiState.value = _uiState.value.copy(isSaving = false)
                        eventChannel.send(UiEvent.NavigateBackWithResult(BIKE_LIST_REFRESH_REQUESTED, true))
                    }

                    is ApiResult.Error ->
                        _uiState.value =
                            _uiState.value.copy(
                                isSaving = false,
                                error = result.message,
                            )

                    ApiResult.Loading ->
                        _uiState.value = _uiState.value.copy(isSaving = true)
                }
            }
        }

        private fun loadBike(bikeId: String) {
            viewModelScope.launch {
                _uiState.value =
                    _uiState.value.copy(
                        isNew = false,
                        isLoading = true,
                        error = null,
                    )

                when (val result = repository.getBike(bikeId)) {
                    is ApiResult.Success -> applyLoadedBike(result.data)
                    is ApiResult.Error ->
                        _uiState.value =
                            _uiState.value.copy(
                                isLoading = false,
                                error = result.message,
                            )

                    ApiResult.Loading ->
                        _uiState.value = _uiState.value.copy(isLoading = true)
                }
            }
        }

        private fun applyLoadedBike(bike: BikeProfile) {
            val profile =
                BikeProfileEdit(
                    displayName = bike.displayName,
                    identity = bike.identity,
                    frame = bike.frame,
                    brakes = bike.brakes,
                    drivetrain = bike.drivetrain,
                    rollingSystem = bike.rollingSystem,
                    suspension = bike.suspension,
                    cockpit = bike.cockpit,
                    seating = bike.seating,
                    electricAssist = bike.electricAssist,
                    notes = bike.legacy.notes,
                )
            val restoredProfile =
                if (hasRestoredDraft) {
                    profile.copy(
                        displayName = savedStateHandle["displayName"] ?: profile.displayName,
                        notes = savedStateHandle["notes"] ?: profile.notes,
                    )
                } else {
                    profile
                }
            savedStateHandle["displayName"] = restoredProfile.displayName
            savedStateHandle["notes"] = restoredProfile.notes

            _uiState.value =
                _uiState.value.copy(
                    isNew = false,
                    profile = restoredProfile,
                    originalProfile = profile,
                    isLoading = false,
                    error = null,
                    validationErrors = emptyMap(),
                )
        }

        private fun clearDraft() {
            FORM_KEYS.forEach { savedStateHandle.remove<String>(it) }
        }

        private fun validate(state: BikeEditUiState): Map<String, String> {
            val errors = mutableMapOf<String, String>()
            if (state.profile.displayName.isBlank()) {
                errors["displayName"] = "Display name is required."
            }

            state.profile.identity.modelYear?.let { modelYear ->
                if (modelYear !in 1880..2100) {
                    errors["modelYear"] = "Model year must be between 1880 and 2100."
                }
            }
            return errors
        }

        companion object {
            private val FORM_KEYS = listOf("displayName", "notes")
        }
    }

private fun BikeProfileEdit.normalized() = copy(displayName = displayName.trim(), notes = notes?.trim()?.takeIf(String::isNotEmpty))
