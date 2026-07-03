package com.bikedoc.android.sessions.chat

import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.api.models.InputRequest
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.api.models.RepairSessionCreate
import com.bikedoc.android.api.models.RepairSessionListResponse
import com.bikedoc.android.api.models.TurnAccepted
import com.bikedoc.android.api.models.TurnCreate
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
) : SessionRepository {
    val loadedSessionIds = mutableListOf<String>()

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
    ): ApiResult<TurnAccepted> = error("Not used by diagnostic chat entry")
}

private fun repairSession(
    status: String = "created",
    currentInputRequest: InputRequest? = null,
) = RepairSession(
    id = "session-1",
    bikeId = "bike-1",
    phase = "diagnostic",
    status = status,
    currentInputRequest = currentInputRequest,
    createdAt = "2026-07-02T00:00:00Z",
    updatedAt = "2026-07-02T00:00:00Z",
)
