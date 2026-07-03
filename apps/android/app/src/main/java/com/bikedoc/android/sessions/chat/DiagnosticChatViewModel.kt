package com.bikedoc.android.sessions.chat

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.api.models.InputRequest
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.core.IoDispatcher
import com.bikedoc.android.sessions.models.ChatMessage
import com.bikedoc.android.sessions.models.Role
import com.bikedoc.android.sessions.models.SseEvent
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.time.Instant
import javax.inject.Inject

data class DiagnosticChatUiState(
    val session: RepairSession? = null,
    val messages: List<ChatMessage> = emptyList(),
    val inputRequest: InputRequest? = null,
    val draftText: String = "",
    val selectedArtifactIds: List<String> = emptyList(),
    val isLoadingSession: Boolean = true,
    val isTurnInFlight: Boolean = false,
    val isStreaming: Boolean = false,
    val streamingBubbleText: String = "",
    val phaseTransitioned: Boolean = false,
    val latestReportId: String? = null,
    val error: String? = null,
)

@HiltViewModel
class DiagnosticChatViewModel
    @Inject
    constructor(
        private val sessionRepository: SessionRepository,
        private val eventSource: SseEventSource,
        @IoDispatcher private val ioDispatcher: CoroutineDispatcher,
        savedStateHandle: SavedStateHandle,
    ) : ViewModel() {
        private val sessionId: String = checkNotNull(savedStateHandle["sessionId"])
        private var eventJob: Job? = null

        private val _uiState = MutableStateFlow(DiagnosticChatUiState())
        val uiState: StateFlow<DiagnosticChatUiState> = _uiState.asStateFlow()

        init {
            loadSessionAndReplayEvents()
        }

        constructor(
            sessionRepository: SessionRepository,
            eventSource: SseEventSource,
            ioDispatcher: CoroutineDispatcher,
            sessionId: String,
        ) : this(
            sessionRepository = sessionRepository,
            eventSource = eventSource,
            ioDispatcher = ioDispatcher,
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to sessionId)),
        )

        override fun onCleared() {
            eventJob?.cancel()
            super.onCleared()
        }

        fun onDraftTextChanged(value: String) {
            _uiState.value = _uiState.value.copy(draftText = value)
        }

        private fun loadSessionAndReplayEvents() {
            viewModelScope.launch(ioDispatcher) {
                _uiState.value =
                    _uiState.value.copy(
                        isLoadingSession = true,
                        error = null,
                    )

                when (val result = sessionRepository.getRepairSession(sessionId)) {
                    is ApiResult.Success -> {
                        applyLoadedSession(result.data)
                        startEventReplay()
                    }

                    is ApiResult.Error ->
                        _uiState.value =
                            _uiState.value.copy(
                                isLoadingSession = false,
                                error = result.message,
                            )

                    ApiResult.Loading ->
                        _uiState.value = _uiState.value.copy(isLoadingSession = true)
                }
            }
        }

        private fun applyLoadedSession(session: RepairSession) {
            _uiState.value =
                _uiState.value.copy(
                    session = session,
                    inputRequest =
                        if (session.status == AWAITING_USER || session.status == AWAITING_DECISION) {
                            session.currentInputRequest
                        } else {
                            null
                        },
                    isLoadingSession = false,
                    error = null,
                )
        }

        private fun startEventReplay() {
            eventJob?.cancel()
            eventJob =
                viewModelScope.launch(ioDispatcher) {
                    eventSource
                        .connect(sessionId = sessionId, after = REPLAY_FROM_BEGINNING)
                        .collect { event -> handleSseEvent(event) }
                }
        }

        private fun handleSseEvent(event: SseEvent) {
            when (event) {
                is SseEvent.AssistantDelta -> applyAssistantDelta(event)
                is SseEvent.AssistantMessageCompleted -> applyAssistantMessageCompleted(event)
                is SseEvent.InputRequested -> applyInputRequested(event)
                is SseEvent.PhaseReportCreated -> applyPhaseReportCreated(event)
                is SseEvent.TurnCompleted -> applyTurnCompleted(event)
                is SseEvent.Error -> applyError(event)
                is SseEvent.PhaseTransitioned -> applyPhaseTransitioned(event)
                is SseEvent.TurnStarted,
                is SseEvent.ArtifactReferenced,
                is SseEvent.SafetyEscalated,
                is SseEvent.Heartbeat,
                is SseEvent.Unknown,
                -> Unit
            }
        }

        private fun applyAssistantDelta(event: SseEvent.AssistantDelta) {
            _uiState.value =
                _uiState.value.copy(
                    isStreaming = true,
                    streamingBubbleText = _uiState.value.streamingBubbleText + event.text,
                )
        }

        private fun applyAssistantMessageCompleted(event: SseEvent.AssistantMessageCompleted) {
            appendMessage(
                ChatMessage(
                    id = event.messageId,
                    role = Role.Assistant,
                    text = event.fullText,
                    artifactIds = event.artifactIds,
                    createdAt = Instant.now(),
                ),
            ) {
                copy(
                    isStreaming = false,
                    streamingBubbleText = "",
                )
            }
        }

        private fun applyInputRequested(event: SseEvent.InputRequested) {
            val currentSession = _uiState.value.session
            _uiState.value =
                _uiState.value.copy(
                    inputRequest = event.inputRequest,
                    session = currentSession?.copy(currentInputRequest = event.inputRequest),
                )
        }

        private fun applyPhaseReportCreated(event: SseEvent.PhaseReportCreated) {
            _uiState.value = _uiState.value.copy(latestReportId = event.reportId)
        }

        private fun applyTurnCompleted(event: SseEvent.TurnCompleted) {
            _uiState.value =
                _uiState.value.copy(
                    isTurnInFlight = false,
                    session = event.session,
                    inputRequest = event.session.currentInputRequest,
                )
        }

        private fun applyError(event: SseEvent.Error) {
            _uiState.value =
                _uiState.value.copy(
                    isTurnInFlight = false,
                    isStreaming = false,
                    streamingBubbleText = "",
                    error = event.message,
                )
        }

        private fun applyPhaseTransitioned(event: SseEvent.PhaseTransitioned) {
            appendMessage(
                ChatMessage(
                    id = event.id ?: "phase-transitioned-${_uiState.value.messages.size + 1}",
                    role = Role.System,
                    text = DIAGNOSTIC_COMPLETE_MESSAGE,
                    createdAt = Instant.now(),
                ),
            ) {
                copy(
                    phaseTransitioned = true,
                    isTurnInFlight = false,
                    isStreaming = false,
                    streamingBubbleText = "",
                    inputRequest = null,
                    session = session?.copy(phase = event.toPhase, status = event.status),
                )
            }
            eventJob?.cancel()
        }

        private fun appendMessage(
            message: ChatMessage,
            transform: DiagnosticChatUiState.() -> DiagnosticChatUiState = { this },
        ) {
            _uiState.value =
                _uiState.value
                    .copy(messages = _uiState.value.messages + message)
                    .transform()
        }

        companion object {
            const val DIAGNOSTIC_COMPLETE_MESSAGE = "Diagnosis complete — your results are ready."
            private const val AWAITING_USER = "awaiting_user"
            private const val AWAITING_DECISION = "awaiting_decision"
            private const val REPLAY_FROM_BEGINNING = "0"
        }
    }
