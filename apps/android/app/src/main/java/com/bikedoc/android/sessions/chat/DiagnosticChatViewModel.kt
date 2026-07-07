package com.bikedoc.android.sessions.chat

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.ArtifactRepository
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.api.models.ArtifactRef
import com.bikedoc.android.api.models.InputRequest
import com.bikedoc.android.api.models.PreparedDiagnosticPhoto
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
import java.io.IOException
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
    val photoAttachments: List<DiagnosticPhotoAttachment> = emptyList(),
    val isLoadingSession: Boolean = true,
    val isTurnInFlight: Boolean = false,
    val isStreaming: Boolean = false,
    val streamingBubbleText: String = "",
    val phaseTransitioned: Boolean = false,
    val latestReportId: String? = null,
    val error: String? = null,
) {
    val canAcceptUserInput: Boolean
        get() =
            !phaseTransitioned &&
                (inputRequest?.type != "none") &&
                (inputRequest != null || session?.status in FREEFORM_INPUT_STATUSES)

    val canSubmitCurrentInput: Boolean
        get() {
            val hasContent = draftText.isNotBlank() || selectedArtifactIds.isNotEmpty()
            val minimumArtifacts = inputRequest?.minArtifacts ?: if (inputRequest.isPhotoRequest()) 1 else 0
            return hasContent &&
                selectedArtifactIds.size >= minimumArtifacts &&
                canAcceptUserInput &&
                !isTurnInFlight &&
                !isStreaming
        }

    companion object {
        const val CREATED = "created"
        const val AWAITING_USER = "awaiting_user"
        private val FREEFORM_INPUT_STATUSES = setOf(CREATED, AWAITING_USER)
    }
}

data class DiagnosticPhotoAttachment(
    val id: String,
    val selection: DiagnosticPhotoSelection,
    val artifactId: String? = null,
    val status: DiagnosticPhotoUploadStatus,
    val error: String? = null,
)

enum class DiagnosticPhotoUploadStatus {
    Uploading,
    Ready,
    Failed,
}

@HiltViewModel
class DiagnosticChatViewModel
    @Inject
    constructor(
        private val sessionRepository: SessionRepository,
        private val artifactRepository: ArtifactRepository,
        private val photoPreparer: DiagnosticPhotoPreparer,
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
            artifactRepository: ArtifactRepository = NoopArtifactRepository,
            photoPreparer: DiagnosticPhotoPreparer = NoopDiagnosticPhotoPreparer,
            eventSource: SseEventSource,
            ioDispatcher: CoroutineDispatcher,
            sessionId: String,
        ) : this(
            sessionRepository = sessionRepository,
            artifactRepository = artifactRepository,
            photoPreparer = photoPreparer,
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

        fun onPhotosSelected(selections: List<DiagnosticPhotoSelection>) {
            if (selections.isEmpty() || !_uiState.value.inputRequest.isPhotoRequest()) {
                return
            }

            val attachments =
                selections.map { selection ->
                    DiagnosticPhotoAttachment(
                        id = UUID.randomUUID().toString(),
                        selection = selection,
                        status = DiagnosticPhotoUploadStatus.Uploading,
                    )
                }
            _uiState.value =
                _uiState.value.copy(
                    photoAttachments = _uiState.value.photoAttachments + attachments,
                    error = null,
                )
            attachments.forEach(::uploadAttachment)
        }

        fun retryPhotoUpload(attachmentId: String) {
            val attachment =
                _uiState.value.photoAttachments.firstOrNull { it.id == attachmentId }
                    ?: return
            _uiState.value =
                _uiState.value.copy(
                    photoAttachments =
                        _uiState.value.photoAttachments.map {
                            if (it.id == attachmentId) {
                                it.copy(status = DiagnosticPhotoUploadStatus.Uploading, error = null)
                            } else {
                                it
                            }
                        },
                    error = null,
                )
            uploadAttachment(attachment.copy(status = DiagnosticPhotoUploadStatus.Uploading, error = null))
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
            val latestReportId = session.latestDisplayReportId()
            _uiState.value =
                _uiState.value.copy(
                    session = session,
                    latestReportId = latestReportId,
                    phaseTransitioned = latestReportId != null && session.phase != PLANNING,
                    inputRequest =
                        if (
                            latestReportId == null &&
                            (session.status == AWAITING_USER || session.status == AWAITING_DECISION)
                        ) {
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
            val isPlanReport = event.reportType == PLAN_REPORT_TYPE || event.schemaVersion == PLAN_SCHEMA_VERSION
            _uiState.value =
                _uiState.value.copy(
                    latestReportId =
                        if (isPlanReport || _uiState.value.latestReportId == null) {
                            event.reportId
                        } else {
                            _uiState.value.latestReportId
                        },
                    phaseTransitioned = _uiState.value.phaseTransitioned || isPlanReport,
                    inputRequest = if (isPlanReport) null else _uiState.value.inputRequest,
                )
            if (isPlanReport) {
                eventJob?.cancel()
            }
        }

        private fun applyTurnCompleted(event: SseEvent.TurnCompleted) {
            val currentInputRequest = _uiState.value.inputRequest
            val completedInputRequest =
                event.session.currentInputRequest
                    ?: currentInputRequest?.takeIf { event.session.isAwaitingUserInput() }
            _uiState.value =
                _uiState.value.copy(
                    isTurnInFlight = false,
                    session = event.session.copy(currentInputRequest = completedInputRequest),
                    inputRequest = completedInputRequest,
                    latestReportId = event.session.latestDisplayReportId() ?: _uiState.value.latestReportId,
                    phaseTransitioned =
                        _uiState.value.phaseTransitioned ||
                            event.session.latestReports.planReportId != null ||
                            (
                                event.session.latestDisplayReportId() != null &&
                                    event.session.phase != PLANNING
                            ),
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
            val shouldWaitForPlanReport =
                event.toPhase == PLANNING &&
                    _uiState.value.session?.latestReports?.planReportId == null
            if (shouldWaitForPlanReport) {
                _uiState.value =
                    _uiState.value.copy(
                        isTurnInFlight = false,
                        isStreaming = false,
                        streamingBubbleText = "",
                        inputRequest = null,
                        session = _uiState.value.session?.copy(phase = event.toPhase, status = event.status),
                    )
                return
            }
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
            val state = _uiState.value
            val hasContent = text.isNotBlank() || artifactIds.isNotEmpty()
            val inputRequest = state.inputRequest
            val minimumArtifacts =
                inputRequest?.minArtifacts ?: if (inputRequest.isPhotoRequest()) 1 else 0
            return hasContent &&
                state.canAcceptUserInput &&
                artifactIds.size >= minimumArtifacts &&
                !state.isTurnInFlight &&
                !state.isStreaming
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
                    photoAttachments =
                        if (optimisticMessageId == null) {
                            emptyList()
                        } else {
                            _uiState.value.photoAttachments
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
                    photoAttachments = emptyList(),
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
            ensureEventReplay(after = acceptedTurn.startEventId)
        }

        private fun ensureEventReplay(after: String?) {
            if (eventJob?.isActive == true) {
                return
            }
            startEventReplay(after = after)
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

        private fun uploadAttachment(attachment: DiagnosticPhotoAttachment) {
            viewModelScope.launch(ioDispatcher) {
                val result =
                    try {
                        val preparedPhoto = photoPreparer.prepare(attachment.selection)
                        artifactRepository.uploadDiagnosticPhoto(sessionId, preparedPhoto)
                    } catch (cancellationException: CancellationException) {
                        throw cancellationException
                    } catch (exception: IOException) {
                        ApiResult.Error(null, exception.message ?: "Could not prepare selected image.")
                    } catch (exception: IllegalArgumentException) {
                        ApiResult.Error(null, exception.message ?: "Could not prepare selected image.")
                    } catch (exception: IllegalStateException) {
                        ApiResult.Error(null, exception.message ?: "Could not prepare selected image.")
                    } catch (exception: SecurityException) {
                        ApiResult.Error(null, exception.message ?: "Could not prepare selected image.")
                    }

                when (result) {
                    is ApiResult.Success -> markAttachmentReady(attachment.id, result.data.id)
                    is ApiResult.Error -> markAttachmentFailed(attachment.id, result.message)
                    ApiResult.Loading -> Unit
                }
            }
        }

        private fun markAttachmentReady(
            attachmentId: String,
            artifactId: String,
        ) {
            _uiState.value =
                _uiState.value.copy(
                    photoAttachments =
                        _uiState.value.photoAttachments.map { attachment ->
                            if (attachment.id == attachmentId) {
                                attachment.copy(
                                    artifactId = artifactId,
                                    status = DiagnosticPhotoUploadStatus.Ready,
                                    error = null,
                                )
                            } else {
                                attachment
                            }
                        },
                ).syncSelectedArtifactIds()
        }

        private fun markAttachmentFailed(
            attachmentId: String,
            errorMessage: String,
        ) {
            _uiState.value =
                _uiState.value.copy(
                    photoAttachments =
                        _uiState.value.photoAttachments.map { attachment ->
                            if (attachment.id == attachmentId) {
                                attachment.copy(
                                    artifactId = null,
                                    status = DiagnosticPhotoUploadStatus.Failed,
                                    error = errorMessage,
                                )
                            } else {
                                attachment
                            }
                        },
                ).syncSelectedArtifactIds()
        }

        companion object {
            const val DIAGNOSTIC_COMPLETE_MESSAGE = "Diagnosis complete — your results are ready."
            private const val AWAITING_USER = "awaiting_user"
            private const val AWAITING_DECISION = "awaiting_decision"
            private const val RUNNING = "running"
            private const val PLANNING = "planning"
            private const val PLAN_REPORT_TYPE = "plan"
            private const val PLAN_SCHEMA_VERSION = "plan_report.v1"
            private const val REPLAY_FROM_BEGINNING = "0"
            private const val INITIAL_RECONNECT_DELAY_MS = 1_000L
            private const val MAX_RECONNECT_DELAY_MS = 30_000L
            private const val RECONNECT_JITTER_MS = 250L
        }
    }

private object NoopArtifactRepository : ArtifactRepository {
    override suspend fun uploadDiagnosticPhoto(
        sessionId: String,
        photo: PreparedDiagnosticPhoto,
    ): ApiResult<ArtifactRef> = ApiResult.Error(500, "Artifact upload is not configured.")
}

private object NoopDiagnosticPhotoPreparer : DiagnosticPhotoPreparer {
    override suspend fun prepare(selection: DiagnosticPhotoSelection): PreparedDiagnosticPhoto =
        error("Photo preparation is not configured.")
}

private fun DiagnosticChatUiState.activeInputRequestId(): String? {
    val sessionRequestId = session?.currentInputRequest?.id
    return inputRequest?.id?.takeIf { it == sessionRequestId }
}

private fun RepairSession.isAwaitingUserInput(): Boolean = status == "awaiting_user" || status == "awaiting_decision"

private fun RepairSession.latestDisplayReportId(): String? {
    return latestReports.planReportId ?: latestReports.diagnosticReportId
}

private fun InputRequest?.isPhotoRequest(): Boolean =
    this?.type.equals("photo", ignoreCase = true) ||
        this?.acceptedMediaTypes.orEmpty().any { it.startsWith("image/") } ||
        this?.minArtifacts != null ||
        this?.maxArtifacts != null

private fun DiagnosticChatUiState.syncSelectedArtifactIds(): DiagnosticChatUiState =
    copy(
        selectedArtifactIds =
            photoAttachments.mapNotNull { attachment ->
                attachment.artifactId?.takeIf { attachment.status == DiagnosticPhotoUploadStatus.Ready }
            },
    )
