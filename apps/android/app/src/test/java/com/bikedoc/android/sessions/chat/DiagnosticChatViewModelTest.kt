package com.bikedoc.android.sessions.chat

import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.api.models.InputRequest
import com.bikedoc.android.api.models.LatestReports
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.api.models.RepairSessionCreate
import com.bikedoc.android.api.models.RepairSessionListResponse
import com.bikedoc.android.api.models.TurnAccepted
import com.bikedoc.android.api.models.TurnCreate
import com.bikedoc.android.sessions.models.DeliveryState
import com.bikedoc.android.sessions.models.Role
import com.bikedoc.android.sessions.models.SseEvent
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class DiagnosticChatViewModelTest {
    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `entry loads session and replays retained events into transcript and current state`() =
        runTest {
            val session =
                repairSession(
                    status = "awaiting_user",
                    currentInputRequest = InputRequest(id = "request-1", type = "text", prompt = "What changed?"),
                )
            val repository = FakeSessionRepository(getRepairSessionResult = ApiResult.Success(session))
            val eventSource = FakeSseEventSource()
            val viewModel =
                DiagnosticChatViewModel(
                    sessionRepository = repository,
                    eventSource = eventSource,
                    ioDispatcher = mainDispatcherRule.dispatcher,
                    sessionId = "session-1",
                )

            assertEquals(listOf("session-1"), repository.loadedSessionIds)
            assertEquals(listOf(EventConnection("session-1", "0")), eventSource.connections)
            assertFalse(viewModel.uiState.value.isLoadingSession)
            assertEquals("request-1", viewModel.uiState.value.inputRequest?.id)

            eventSource.events.emit(
                SseEvent.AssistantMessageCompleted(
                    id = "2",
                    messageId = "assistant-1",
                    fullText = "Check the rear derailleur hanger.",
                    artifactIds = emptyList(),
                ),
            )
            eventSource.events.emit(
                SseEvent.InputRequested(
                    id = "3",
                    inputRequest = InputRequest(id = "request-2", type = "photo", prompt = "Add a photo."),
                ),
            )
            assertEquals("request-2", viewModel.uiState.value.inputRequest?.id)

            eventSource.events.emit(
                SseEvent.PhaseTransitioned(
                    id = "4",
                    fromPhase = "diagnostic",
                    toPhase = "plan",
                    status = "completed",
                ),
            )

            val state = viewModel.uiState.value
            assertEquals(null, state.inputRequest)
            assertTrue(state.phaseTransitioned)
            assertFalse(state.isStreaming)
            assertEquals(2, state.messages.size)
            assertEquals(Role.Assistant, state.messages[0].role)
            assertEquals("Check the rear derailleur hanger.", state.messages[0].text)
            assertEquals(Role.System, state.messages[1].role)
            assertEquals(DiagnosticChatViewModel.DIAGNOSTIC_COMPLETE_MESSAGE, state.messages[1].text)
        }

    @Test
    @Suppress("LongMethod")
    fun `sending text turn appends optimistic message posts turn and reconnects stream from accepted event`() =
        runTest {
            val session =
                repairSession(
                    status = "awaiting_user",
                    currentInputRequest =
                        inputRequest(
                            id = "request-1",
                            type = "text",
                            prompt = "What changed?",
                        ),
                )
            val acceptedSession =
                repairSession(
                    status = "running",
                    currentInputRequest = null,
                    latestEventId = "5",
                )
            val repository =
                FakeSessionRepository(
                    getRepairSessionResult = ApiResult.Success(session),
                    createTurnResult =
                        ApiResult.Success(
                            TurnAccepted(
                                turnId = "turn-1",
                                repairSessionId = session.id,
                                startEventId = "4",
                                eventStreamUrl =
                                    "/v1/repair-sessions/${session.id}/events?after=4",
                                session = acceptedSession,
                            ),
                        ),
                )
            val eventSource = FakeSseEventSource()
            val viewModel =
                DiagnosticChatViewModel(
                    sessionRepository = repository,
                    eventSource = eventSource,
                    ioDispatcher = mainDispatcherRule.dispatcher,
                    sessionId = session.id,
                )

            viewModel.onDraftTextChanged("The chain skips under load.")
            viewModel.submitTextTurn()

            val state = viewModel.uiState.value
            assertEquals("", state.draftText)
            assertTrue(state.isTurnInFlight)
            assertEquals(1, state.messages.size)
            assertEquals(Role.User, state.messages.single().role)
            assertEquals("The chain skips under load.", state.messages.single().text)
            assertEquals(
                listOf(
                    TurnRequest(
                        sessionId = session.id,
                        body =
                            TurnCreate(
                                clientTurnId = repository.createdTurns.single().body.clientTurnId,
                                message =
                                    com.bikedoc.android.api.models.UserTurnMessage(
                                        text = "The chain skips under load.",
                                    ),
                                respondsToInputRequestId = "request-1",
                            ),
                    ),
                ),
                repository.createdTurns,
            )
            assertEquals(acceptedSession, state.session)
            assertEquals(null, state.inputRequest)
            assertEquals(
                listOf(
                    EventConnection(session.id, "0"),
                    EventConnection(session.id, "4"),
                ),
                eventSource.connections,
            )
        }

    @Test
    @Suppress("LongMethod")
    fun `failed turn keeps optimistic message visible and retry resubmits it`() =
        runTest {
            val session =
                repairSession(
                    status = "awaiting_user",
                    currentInputRequest = inputRequest(id = "request-1"),
                )
            val acceptedSession =
                repairSession(
                    status = "running",
                    currentInputRequest = null,
                    latestEventId = "7",
                )
            val repository =
                FakeSessionRepository(
                    getRepairSessionResult = ApiResult.Success(session),
                    createTurnResults =
                        ArrayDeque(
                            listOf(
                                ApiResult.Error(409, "Session state conflict."),
                                ApiResult.Success(
                                    TurnAccepted(
                                        turnId = "turn-2",
                                        repairSessionId = session.id,
                                        startEventId = "6",
                                        eventStreamUrl =
                                            "/v1/repair-sessions/${session.id}/events?after=6",
                                        session = acceptedSession,
                                    ),
                                ),
                            ),
                        ),
                )
            val viewModel =
                DiagnosticChatViewModel(
                    sessionRepository = repository,
                    eventSource = FakeSseEventSource(),
                    ioDispatcher = mainDispatcherRule.dispatcher,
                    sessionId = session.id,
                )

            viewModel.onDraftTextChanged("It clicks once per pedal stroke.")
            viewModel.submitTextTurn()

            val failedMessage = viewModel.uiState.value.messages.single()
            assertEquals(Role.User, failedMessage.role)
            assertEquals(DeliveryState.Failed, failedMessage.deliveryState)
            assertEquals("Session state conflict.", viewModel.uiState.value.error)
            assertFalse(viewModel.uiState.value.isTurnInFlight)

            viewModel.retryMessage(failedMessage.id)

            val retriedState = viewModel.uiState.value
            assertEquals(1, retriedState.messages.size)
            assertEquals(DeliveryState.Sent, retriedState.messages.single().deliveryState)
            assertTrue(retriedState.isTurnInFlight)
            assertEquals(2, repository.createdTurns.size)
            assertEquals(
                "It clicks once per pedal stroke.",
                repository.createdTurns.last().body.message.text,
            )
            assertEquals(
                "request-1",
                repository.createdTurns.last().body.respondsToInputRequestId,
            )
        }

    @Test
    fun `submitting choice turn posts selected value immediately`() =
        runTest {
            val session =
                repairSession(
                    status = "awaiting_decision",
                    currentInputRequest =
                        inputRequest(
                            id = "request-2",
                            type = "decision",
                        ),
                )
            val repository =
                FakeSessionRepository(
                    getRepairSessionResult = ApiResult.Success(session),
                    createTurnResult =
                        ApiResult.Success(
                            TurnAccepted(
                                turnId = "turn-3",
                                repairSessionId = session.id,
                                startEventId = "8",
                                eventStreamUrl =
                                    "/v1/repair-sessions/${session.id}/events?after=8",
                                session =
                                    repairSession(
                                        status = "running",
                                        currentInputRequest = null,
                                        latestEventId = "8",
                                    ),
                            ),
                        ),
                )
            val viewModel =
                DiagnosticChatViewModel(
                    sessionRepository = repository,
                    eventSource = FakeSseEventSource(),
                    ioDispatcher = mainDispatcherRule.dispatcher,
                    sessionId = session.id,
                )

            viewModel.submitChoiceTurn("yes")

            assertEquals("yes", repository.createdTurns.single().body.message.text)
            assertEquals("request-2", repository.createdTurns.single().body.respondsToInputRequestId)
            assertEquals("yes", viewModel.uiState.value.messages.single().text)
        }

    @Test
    fun `turn started clears pending input request and disables further submission`() =
        runTest {
            val session =
                repairSession(
                    status = "awaiting_user",
                    currentInputRequest = inputRequest(id = "request-1"),
                )
            val repository =
                FakeSessionRepository(
                    getRepairSessionResult = ApiResult.Success(session),
                    createTurnResult =
                        ApiResult.Success(
                            turnAccepted(
                                turnId = "turn-2",
                                session = repairSession(status = "running"),
                            ),
                        ),
                )
            val eventSource = FakeSseEventSource()
            val viewModel =
                DiagnosticChatViewModel(
                    sessionRepository = repository,
                    eventSource = eventSource,
                    ioDispatcher = mainDispatcherRule.dispatcher,
                    sessionId = session.id,
                )

            eventSource.events.emit(
                SseEvent.TurnStarted(
                    id = "2",
                    turnId = "turn-1",
                    phase = "diagnostic",
                ),
            )

            assertEquals(null, viewModel.uiState.value.inputRequest)
            assertTrue(viewModel.uiState.value.isTurnInFlight)
            assertEquals("running", viewModel.uiState.value.session?.status)
            assertEquals(emptyList<TurnRequest>(), repository.createdTurns)
        }
}

private data class EventConnection(
    val sessionId: String,
    val after: String?,
)

private class FakeSseEventSource : SseEventSource {
    val events = MutableSharedFlow<SseEvent>(extraBufferCapacity = 16)
    val connections = mutableListOf<EventConnection>()

    override fun connect(
        sessionId: String,
        after: String?,
    ): Flow<SseEvent> {
        connections += EventConnection(sessionId, after)
        return events
    }
}

private class FakeSessionRepository(
    private val getRepairSessionResult: ApiResult<RepairSession>,
    createTurnResult: ApiResult<TurnAccepted> = ApiResult.Error(500, "Missing createTurnResult"),
    createTurnResults: ArrayDeque<ApiResult<TurnAccepted>> = ArrayDeque(listOf(createTurnResult)),
) : SessionRepository {
    val loadedSessionIds = mutableListOf<String>()
    val createdTurns = mutableListOf<TurnRequest>()
    private val queuedCreateTurnResults = createTurnResults

    override suspend fun getRepairSessions(bikeId: String): ApiResult<RepairSessionListResponse> {
        error("Not used by diagnostic chat")
    }

    override suspend fun createRepairSession(body: RepairSessionCreate): ApiResult<RepairSession> {
        error("Not used by diagnostic chat")
    }

    override suspend fun getRepairSession(sessionId: String): ApiResult<RepairSession> {
        loadedSessionIds += sessionId
        return getRepairSessionResult
    }

    override suspend fun createTurn(
        sessionId: String,
        body: TurnCreate,
    ): ApiResult<TurnAccepted> {
        createdTurns += TurnRequest(sessionId, body)
        return queuedCreateTurnResults.removeFirst()
    }
}

private fun repairSession(
    status: String = "created",
    currentInputRequest: InputRequest? = null,
    latestEventId: String = "0",
) = RepairSession(
    id = "session-1",
    userId = "user-1",
    bikeId = "bike-1",
    phase = "diagnostic",
    status = status,
    safetyState = "ok",
    currentInputRequest = currentInputRequest,
    latestReports = LatestReports(),
    latestEventId = latestEventId,
    createdAt = "2026-07-02T00:00:00Z",
    updatedAt = "2026-07-02T00:00:00Z",
)

private fun inputRequest(
    id: String = "request-1",
    type: String = "text",
    prompt: String = "Prompt",
) = InputRequest(
    id = id,
    type = type,
    prompt = prompt,
    required = true,
    acceptedMediaTypes = emptyList(),
    createdAt = "2026-07-02T00:00:00Z",
)

private fun turnAccepted(
    turnId: String,
    session: RepairSession,
    startEventId: String = "3",
) = TurnAccepted(
    turnId = turnId,
    repairSessionId = session.id,
    startEventId = startEventId,
    eventStreamUrl = "/v1/repair-sessions/${session.id}/events?after=$startEventId",
    session = session,
)

private data class TurnRequest(
    val sessionId: String,
    val body: TurnCreate,
)
