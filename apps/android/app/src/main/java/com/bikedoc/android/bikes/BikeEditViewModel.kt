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
    val displayName: String = "",
    val make: String = "",
    val model: String = "",
    val modelYear: String = "",
    val bikeType: String = "unknown",
    val frameMaterial: String = "unknown",
    val drivetrain: String = "",
    val brakeType: String = "unknown",
    val wheelSize: String = "",
    val tireSize: String = "",
    val notes: String = "",
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
                    displayName = savedStateHandle["displayName"] ?: "",
                    make = savedStateHandle["make"] ?: "",
                    model = savedStateHandle["model"] ?: "",
                    modelYear = savedStateHandle["modelYear"] ?: "",
                    bikeType = savedStateHandle["bikeType"] ?: "unknown",
                    frameMaterial = savedStateHandle["frameMaterial"] ?: "unknown",
                    drivetrain = savedStateHandle["drivetrain"] ?: "",
                    brakeType = savedStateHandle["brakeType"] ?: "unknown",
                    wheelSize = savedStateHandle["wheelSize"] ?: "",
                    tireSize = savedStateHandle["tireSize"] ?: "",
                    notes = savedStateHandle["notes"] ?: "",
                    isLoading = bikeId != null && !hasRestoredDraft,
                ),
            )
        val uiState: StateFlow<BikeEditUiState> = _uiState.asStateFlow()

        private val eventChannel = Channel<UiEvent>(Channel.BUFFERED)
        val events = eventChannel.receiveAsFlow()

        init {
            if (bikeId != null && !hasRestoredDraft) {
                loadBike(bikeId)
            }
        }

        fun onDisplayNameChanged(value: String) = updateField("displayName", value) { copy(displayName = value) }

        fun onMakeChanged(value: String) = updateField("make", value) { copy(make = value) }

        fun onModelChanged(value: String) = updateField("model", value) { copy(model = value) }

        fun onModelYearChanged(value: String) = updateField("modelYear", value) { copy(modelYear = value) }

        fun onBikeTypeChanged(value: String) = updateField("bikeType", value) { copy(bikeType = value) }

        fun onFrameMaterialChanged(value: String) = updateField("frameMaterial", value) { copy(frameMaterial = value) }

        fun onDrivetrainChanged(value: String) = updateField("drivetrain", value) { copy(drivetrain = value) }

        fun onBrakeTypeChanged(value: String) = updateField("brakeType", value) { copy(brakeType = value) }

        fun onWheelSizeChanged(value: String) = updateField("wheelSize", value) { copy(wheelSize = value) }

        fun onTireSizeChanged(value: String) = updateField("tireSize", value) { copy(tireSize = value) }

        fun onNotesChanged(value: String) = updateField("notes", value) { copy(notes = value) }

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
                        repository.createBike(_uiState.value.toBikeCreate())
                    } else {
                        repository.updateBike(bikeId, _uiState.value.toBikePatch())
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
            savedStateHandle["displayName"] = bike.displayName
            savedStateHandle["make"] = bike.legacy.make.orEmpty()
            savedStateHandle["model"] = bike.legacy.model.orEmpty()
            savedStateHandle["modelYear"] = bike.legacy.modelYear?.toString().orEmpty()
            savedStateHandle["bikeType"] = bike.legacy.bikeType
            savedStateHandle["frameMaterial"] = bike.legacy.frameMaterial ?: "unknown"
            savedStateHandle["drivetrain"] = bike.legacy.drivetrain.orEmpty()
            savedStateHandle["brakeType"] = bike.legacy.brakeType ?: "unknown"
            savedStateHandle["wheelSize"] = bike.legacy.wheelSize.orEmpty()
            savedStateHandle["tireSize"] = bike.legacy.tireSize.orEmpty()
            savedStateHandle["notes"] = bike.legacy.notes.orEmpty()

            _uiState.value =
                _uiState.value.copy(
                    isNew = false,
                    displayName = bike.displayName,
                    make = bike.legacy.make.orEmpty(),
                    model = bike.legacy.model.orEmpty(),
                    modelYear = bike.legacy.modelYear?.toString().orEmpty(),
                    bikeType = bike.legacy.bikeType,
                    frameMaterial = bike.legacy.frameMaterial ?: "unknown",
                    drivetrain = bike.legacy.drivetrain.orEmpty(),
                    brakeType = bike.legacy.brakeType ?: "unknown",
                    wheelSize = bike.legacy.wheelSize.orEmpty(),
                    tireSize = bike.legacy.tireSize.orEmpty(),
                    notes = bike.legacy.notes.orEmpty(),
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
            if (state.displayName.isBlank()) {
                errors["displayName"] = "Display name is required."
            }

            val modelYearValue = state.modelYear.trim()
            if (modelYearValue.isNotEmpty()) {
                val parsed = modelYearValue.toIntOrNull()
                if (parsed == null || parsed !in 1880..2100) {
                    errors["modelYear"] = "Model year must be between 1880 and 2100."
                }
            }
            return errors
        }

        private fun updateField(
            key: String,
            value: String,
            transform: BikeEditUiState.() -> BikeEditUiState,
        ) {
            savedStateHandle[key] = value
            _uiState.value =
                _uiState.value
                    .transform()
                    .copy(
                        error = _uiState.value.error,
                        validationErrors = _uiState.value.validationErrors - key,
                    )
        }

        private fun BikeEditUiState.toBikeCreate(): BikeProfileEdit =
            BikeProfileEdit(
                displayName = displayName.trim(),
                make = make.trim().takeIf { it.isNotEmpty() },
                model = model.trim().takeIf { it.isNotEmpty() },
                modelYear = modelYear.trim().toIntOrNull(),
                bikeType = bikeType,
                frameMaterial = frameMaterial,
                drivetrain = drivetrain.trim().takeIf { it.isNotEmpty() },
                brakeType = brakeType,
                wheelSize = wheelSize.trim().takeIf { it.isNotEmpty() },
                tireSize = tireSize.trim().takeIf { it.isNotEmpty() },
                notes = notes.trim().takeIf { it.isNotEmpty() },
            )

        private fun BikeEditUiState.toBikePatch(): BikeProfileEdit =
            BikeProfileEdit(
                displayName = displayName.trim(),
                make = make.trim().takeIf { it.isNotEmpty() },
                model = model.trim().takeIf { it.isNotEmpty() },
                modelYear = modelYear.trim().toIntOrNull(),
                bikeType = bikeType,
                frameMaterial = frameMaterial,
                drivetrain = drivetrain.trim().takeIf { it.isNotEmpty() },
                brakeType = brakeType,
                wheelSize = wheelSize.trim().takeIf { it.isNotEmpty() },
                tireSize = tireSize.trim().takeIf { it.isNotEmpty() },
                notes = notes.trim().takeIf { it.isNotEmpty() },
            )

        companion object {
            private val FORM_KEYS =
                listOf(
                    "displayName",
                    "make",
                    "model",
                    "modelYear",
                    "bikeType",
                    "frameMaterial",
                    "drivetrain",
                    "brakeType",
                    "wheelSize",
                    "tireSize",
                    "notes",
                )
        }
    }
