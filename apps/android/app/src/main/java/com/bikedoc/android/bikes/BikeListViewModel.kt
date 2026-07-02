package com.bikedoc.android.bikes

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.navigation.UiEvent
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class BikeListUiState(
    val bikes: List<BikeListItem> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val selectionMode: Boolean = false,
    val pendingDeleteBike: BikeListItem? = null,
    val deletingBikeId: String? = null,
)

@HiltViewModel
class BikeListViewModel
    @Inject
    constructor(
        private val repository: BikeListRepository,
        savedStateHandle: SavedStateHandle,
    ) : ViewModel() {
        private val _uiState =
            MutableStateFlow(
                BikeListUiState(
                    selectionMode = savedStateHandle["selectionMode"] ?: false,
                ),
            )
        val uiState: StateFlow<BikeListUiState> = _uiState.asStateFlow()

        private val eventChannel = Channel<UiEvent>(Channel.BUFFERED)
        val events = eventChannel.receiveAsFlow()

        init {
            refresh()
        }

        constructor(
            repository: BikeListRepository,
            selectionMode: Boolean,
        ) : this(
            repository = repository,
            savedStateHandle = SavedStateHandle(mapOf("selectionMode" to selectionMode)),
        )

        fun refresh() {
            refresh(
                showLoading = true,
                preserveCurrentBikesOnError = false,
                updateErrorState = true,
            )
        }

        fun requestDelete(bike: BikeListItem) {
            if (_uiState.value.selectionMode || bike.hasRepairSessions) {
                return
            }
            _uiState.value = _uiState.value.copy(pendingDeleteBike = bike)
        }

        fun dismissDelete() {
            _uiState.value = _uiState.value.copy(pendingDeleteBike = null)
        }

        fun confirmDelete() {
            val bike = _uiState.value.pendingDeleteBike ?: return
            viewModelScope.launch {
                _uiState.value =
                    _uiState.value.copy(
                        pendingDeleteBike = null,
                        deletingBikeId = bike.id,
                    )

                when (val result = repository.deleteBike(bike.id)) {
                    BikeDeleteResult.Success ->
                        _uiState.value =
                            _uiState.value.copy(
                                bikes = _uiState.value.bikes.filterNot { it.id == bike.id },
                                deletingBikeId = null,
                            )

                    BikeDeleteResult.RepairHistoryConflict -> {
                        _uiState.value =
                            _uiState.value.copy(
                                bikes =
                                    _uiState.value.bikes.map {
                                        if (it.id == bike.id) {
                                            it.copy(hasRepairSessions = true)
                                        } else {
                                            it
                                        }
                                    },
                                deletingBikeId = null,
                            )
                        eventChannel.send(UiEvent.ShowSnackbar(DELETE_REPAIR_HISTORY_MESSAGE))
                        refresh(
                            showLoading = false,
                            preserveCurrentBikesOnError = true,
                            updateErrorState = false,
                        )
                    }

                    is BikeDeleteResult.Error -> {
                        _uiState.value = _uiState.value.copy(deletingBikeId = null)
                        eventChannel.send(UiEvent.ShowSnackbar(result.message))
                    }
                }
            }
        }

        private fun refresh(
            showLoading: Boolean,
            preserveCurrentBikesOnError: Boolean,
            updateErrorState: Boolean,
        ) {
            viewModelScope.launch {
                _uiState.value =
                    _uiState.value.copy(
                        isLoading = showLoading,
                        error = null,
                    )

                when (val result = repository.getBikes()) {
                    is ApiResult.Success ->
                        _uiState.value =
                            _uiState.value.copy(
                                bikes = result.data,
                                isLoading = false,
                                error = null,
                            )

                    is ApiResult.Error ->
                        _uiState.value =
                            _uiState.value.copy(
                                bikes =
                                    if (preserveCurrentBikesOnError) {
                                        _uiState.value.bikes
                                    } else {
                                        emptyList()
                                    },
                                isLoading = false,
                                error = if (updateErrorState) result.message else null,
                            )

                    ApiResult.Loading ->
                        _uiState.value =
                            _uiState.value.copy(
                                isLoading = true,
                                error = null,
                            )
                }
            }
        }

        companion object {
            const val DELETE_REPAIR_HISTORY_MESSAGE =
                "This bike can't be removed because it has repair session history."
        }
    }
