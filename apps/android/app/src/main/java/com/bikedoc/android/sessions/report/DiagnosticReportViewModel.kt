package com.bikedoc.android.sessions.report

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.DiagnosticReport
import com.bikedoc.android.api.ReportRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class DiagnosticReportUiState(
    val report: DiagnosticReport? = null,
    val isLoading: Boolean = true,
    val error: String? = null,
)

@HiltViewModel
class DiagnosticReportViewModel
    @Inject
    constructor(
        private val reportRepository: ReportRepository,
        savedStateHandle: SavedStateHandle,
    ) : ViewModel() {
        private val sessionId: String = checkNotNull(savedStateHandle["sessionId"])
        private val reportId: String = checkNotNull(savedStateHandle["reportId"])

        private val _uiState = MutableStateFlow(DiagnosticReportUiState())
        val uiState: StateFlow<DiagnosticReportUiState> = _uiState.asStateFlow()

        init {
            loadReport()
        }

        fun retry() {
            loadReport()
        }

        private fun loadReport() {
            viewModelScope.launch {
                _uiState.value = _uiState.value.copy(isLoading = true, error = null)
                when (val result = reportRepository.getDiagnosticReport(sessionId, reportId)) {
                    is ApiResult.Success ->
                        _uiState.value =
                            DiagnosticReportUiState(
                                report = result.data,
                                isLoading = false,
                                error = null,
                            )

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
    }
