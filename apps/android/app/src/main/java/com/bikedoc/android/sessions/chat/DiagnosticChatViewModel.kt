package com.bikedoc.android.sessions.chat

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.api.models.InputRequest
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.api.models.TurnCreate
import com.bikedoc.android.api.models.UserTurnMessage
import com.bikedoc.android.core.IoDispatcher
import com.bikedoc.android.sessions.models.ChatMessage
import com.bikedoc.android.sessions.models.DeliveryState
import com.bikedoc.android.sessions.models.Role
import com.bikedoc.android.sessions.models.SseEvent
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.time.Instant
import java.util.UUID
import javax.inject.Inject
import kotlin.random.Random

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
        private var lastEventId: String? = null

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

        fun submitTextTurn() {
            submitTurn(
                text = _uiState.value.draftText.trim(),
                artifactIds = _uiState.value.selectedArtifactIds,
                respondsToInputRequestId = _uiState.value.activeInputRequestId(),
            )
        }

        fun submitChoiceTurn(choiceValue: String) {
            submitTurn(
                text = choiceValue,
                artifactIds = emptyList(),
                respondsToInputRequestId = _uiState.value.activeInputRequestId(),
            )
        }

        fun retryMessage(messageId: String) {
            val message = _uiState.value.messages.firstOrNull { it.id == messageId } ?: return
            if (message.role != Role.User || message.deliveryState != DeliveryState.Failed) {
                return
            }

            markMessageDeliveryState(messageId, DeliveryState.Sending)
            _uiState.value = _uiState.value.copy(error = null)
            submitTurn(
                text = message.text,
                artifactIds = message.artifactIds,
                respondsToInputRequestId = message.respondsToInputRequestId,
                optimisticMessageId = messageId,
            )
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
                        startEventReplay(after = REPLAY_FROM_BEGINNING)
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
            lastEventId = session.latestEventId
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

        private fun startEventReplay(after: String?) {
            eventJob?.cancel()
            eventJob =
                viewModelScope.launch(ioDispatcher) {
                    var reconnectDelayMillis = INITIAL_RECONNECT_DELAY_MS
                    var nextCursor: String? = after

                    while (!_uiState.value.phaseTransitioned) {
                        val cursor = nextCursor ?: lastEventId ?: REPLAY_FROM_BEGINNING
                        try {
                            eventSource
                                .connect(sessionId = sessionId, after = cursor)
                                .collect { event ->
                                    reconnectDelayMillis = INITIAL_RECONNECT_DELAY_MS
                                    nextCursor = null
                                    handleSseEvent(event)
                                }
                        } catch (cancellationException: CancellationException) {
                            throw cancellationException
                        } catch (_: Throwable) {
                            delay(reconnectDelayMillis + Random.nextLong(RECONNECT_JITTER_MS + 1))
                            reconnectDelayMillis =
                                (reconnectDelayMillis * 2).coerceAtMost(MAX_RECONNECT_DELAY_MS)
                        }
                    }
                }
        }

        private fun handleSseEvent(event: SseEvent) {
            event.id?.let { lastEventId = it }
            when (event) {
                is SseEvent.TurnStarted -> applyTurnStarted()
                is SseEvent.AssistantDelta -> applyAssistantDelta(event)
                is SseEvent.AssistantMessageCompleted -> applyAssistantMessageCompleted(event)
                is SseEvent.InputRequested -> applyInputRequested(event)
                is SseEvent.PhaseReportCreated -> applyPhaseReportCreated(event)
                is SseEvent.TurnCompleted -> applyTurnCompleted(event)
                is SseEvent.Error -> applyError(event)
                is SseEvent.PhaseTransitioned -> applyPhaseTransitioned(event)
                is SseEvent.ArtifactReferenced,
                is SseEvent.SafetyEscalated,
                is SseEvent.Heartbeat,
                is SseEvent.Unknown,
                -> Unit
            }
        }

        private fun applyTurnStarted() {
            _uiState.value =
                _uiState.value.copy(
                    isTurnInFlight = true,
                    inputRequest = null,
                    session =
                        _uiState.value.session?.copy(
                            status = RUNNING,
                            currentInputRequest = null,
                        ),
                )
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
                    deliveryState = DeliveryState.Sent,
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
                    deliveryState = DeliveryState.Sent,
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

        private fun submitTurn(
            text: String,
            artifactIds: List<String>,
            respondsToInputRequestId: String?,
            optimisticMessageId: String? = null,
        ) {
            if (!canSubmitTurn(text = text, artifactIds = artifactIds)) {
                return
            }

            val messageId = optimisticMessageId ?: UUID.randomUUID().toString()
            if (optimisticMessageId == null) {
                appendOptimisticMessage(buildOptimisticMessage(messageId, text, artifactIds, respondsToInputRequestId))
            }

            viewModelScope.launch(ioDispatcher) {
                setTurnSubmissionInFlight(optimisticMessageId)

                when (
                    val result =
                        sessionRepository.createTurn(
                            sessionId = sessionId,
                            body = createTurnRequest(text, artifactIds, respondsToInputRequestId),
                        )
                ) {
                    is ApiResult.Success -> handleAcceptedTurn(messageId, result.data)
                    is ApiResult.Error -> handleRejectedTurn(messageId, result.message)
                    ApiResult.Loading -> Unit
                }
            }
        }

        private fun canSubmitTurn(
            text: String,
            artifactIds: List<String>,
        ): Boolean {
            if (text.isBlank() && artifactIds.isEmpty()) {
                return false
            }
            return !_uiState.value.isTurnInFlight && !_uiState.value.isStreaming
        }

        private fun buildOptimisticMessage(
            messageId: String,
            text: String,
            artifactIds: List<String>,
            respondsToInputRequestId: String?,
        ) = ChatMessage(
            id = messageId,
            role = Role.User,
            text = text,
            artifactIds = artifactIds,
            deliveryState = DeliveryState.Sending,
            respondsToInputRequestId = respondsToInputRequestId,
            createdAt = Instant.now(),
        )

        private fun setTurnSubmissionInFlight(optimisticMessageId: String?) {
            _uiState.value =
                _uiState.value.copy(
                    draftText = if (optimisticMessageId == null) "" else _uiState.value.draftText,
                    selectedArtifactIds =
                        if (optimisticMessageId == null) {
                            emptyList()
                        } else {
                            _uiState.value.selectedArtifactIds
                        },
                    isTurnInFlight = true,
                    error = null,
                )
        }

        private fun appendOptimisticMessage(message: ChatMessage) {
            _uiState.value =
                _uiState.value.copy(
                    messages = _uiState.value.messages + message,
                    draftText = "",
                    selectedArtifactIds = emptyList(),
                )
        }

        private fun createTurnRequest(
            text: String,
            artifactIds: List<String>,
            respondsToInputRequestId: String?,
        ) = TurnCreate(
            clientTurnId = UUID.randomUUID().toString(),
            message = UserTurnMessage(text = text, artifactIds = artifactIds),
            respondsToInputRequestId = respondsToInputRequestId,
        )

        private fun handleAcceptedTurn(
            messageId: String,
            acceptedTurn: com.bikedoc.android.api.models.TurnAccepted,
        ) {
            markMessageDeliveryState(messageId, DeliveryState.Sent)
            _uiState.value =
                _uiState.value.copy(
                    session = acceptedTurn.session,
                    inputRequest = acceptedTurn.session.currentInputRequest,
                    isTurnInFlight = true,
                )
            startEventReplay(after = acceptedTurn.startEventId)
        }

        private fun handleRejectedTurn(
            messageId: String,
            errorMessage: String,
        ) {
            markMessageDeliveryState(messageId, DeliveryState.Failed)
            _uiState.value =
                _uiState.value.copy(
                    isTurnInFlight = false,
                    error = errorMessage,
                )
        }

        private fun markMessageDeliveryState(
            messageId: String,
            deliveryState: DeliveryState,
        ) {
            _uiState.value =
                _uiState.value.copy(
                    messages =
                        _uiState.value.messages.map { message ->
                            if (message.id == messageId) {
                                message.copy(deliveryState = deliveryState)
                            } else {
                                message
                            }
                        },
                )
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
            private const val RUNNING = "running"
            private const val REPLAY_FROM_BEGINNING = "0"
            private const val INITIAL_RECONNECT_DELAY_MS = 1_000L
            private const val MAX_RECONNECT_DELAY_MS = 30_000L
            private const val RECONNECT_JITTER_MS = 250L
        }
    }

private fun DiagnosticChatUiState.activeInputRequestId(): String? {
    val sessionRequestId = session?.currentInputRequest?.id
    return inputRequest?.id?.takeIf { it == sessionRequestId }
}
