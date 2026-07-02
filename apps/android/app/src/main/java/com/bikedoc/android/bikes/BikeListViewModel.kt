package com.bikedoc.android.bikes

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.api.ApiResult
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class BikeListUiState(
    val bikes: List<BikeListItem> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val selectionMode: Boolean = false,
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
            viewModelScope.launch {
                _uiState.value =
                    _uiState.value.copy(
                        isLoading = true,
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
                                bikes = emptyList(),
                                isLoading = false,
                                error = result.message,
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
    }
