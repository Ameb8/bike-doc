package com.bikedoc.android.bikes

import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.navigation.AppRoute
import com.bikedoc.android.navigation.UiEvent
import com.bikedoc.android.sessions.models.BikeSession
import com.bikedoc.android.sessions.models.BikeSessionCreate
import com.bikedoc.android.sessions.models.BikeSessionListResponse
import com.bikedoc.android.sessions.models.TurnAccepted
import com.bikedoc.android.sessions.models.TurnCreate
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class BikeListViewModelTest {
    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `resume discovery queries repair for a bike with session history`() =
        runTest {
            val bike = bikeListItem("bike-1", "Commuter", hasSessions = true)
            val sessions = FakeSessionRepository()
            val viewModel =
                BikeListViewModel(
                    repository = FakeBikeListRepository(getBikesResults = mutableListOf(ApiResult.Success(listOf(bike)))),
                    sessionRepository = sessions,
                    selectionMode = true,
                    resumeOnly = true,
                )
            assertEquals(listOf(bike), viewModel.uiState.value.bikes)
            viewModel.selectBike(bike)
            assertEquals(listOf("repair"), sessions.requestedWorkflows)
            assertEquals(null, viewModel.uiState.value.sessionChooser)
        }

    @Test
    fun `loads first page of bikes on init in browse mode`() =
        runTest {
            val repository =
                FakeBikeListRepository(
                    getBikesResults =
                        mutableListOf(
                            ApiResult.Success(
                                listOf(
                                    BikeListItem(
                                        id = "bike-1",
                                        name = "Daily Rider",
                                        makeModelYear = "Trek Domane 2022",
                                        specificationSummary = "Shimano 105 2x11 • Hydraulic disc",
                                        hasSessions = true,
                                    ),
                                ),
                            ),
                        ),
                )

            val viewModel =
                BikeListViewModel(
                    repository = repository,
                    sessionRepository = FakeSessionRepository(),
                    selectionMode = false,
                )

            assertEquals(1, repository.getBikesCalls)
            assertFalse(viewModel.uiState.value.isLoading)
            assertEquals(null, viewModel.uiState.value.error)
            assertEquals(false, viewModel.uiState.value.selectionMode)
            assertEquals("Daily Rider", viewModel.uiState.value.bikes.single().name)
        }

    @Test
    fun `exposes empty browse state when backend returns no bikes`() =
        runTest {
            val viewModel =
                BikeListViewModel(
                    repository =
                        FakeBikeListRepository(
                            getBikesResults = mutableListOf(ApiResult.Success(emptyList())),
                        ),
                    sessionRepository = FakeSessionRepository(),
                    selectionMode = false,
                )

            assertFalse(viewModel.uiState.value.isLoading)
            assertTrue(viewModel.uiState.value.bikes.isEmpty())
            assertEquals(null, viewModel.uiState.value.error)
        }

    @Test
    fun `exposes failed browse state when backend request fails`() =
        runTest {
            val viewModel =
                BikeListViewModel(
                    repository =
                        FakeBikeListRepository(
                            getBikesResults =
                                mutableListOf(
                                    ApiResult.Error(500, "Something went wrong. Try again."),
                                ),
                        ),
                    sessionRepository = FakeSessionRepository(),
                    selectionMode = false,
                )

            assertFalse(viewModel.uiState.value.isLoading)
            assertTrue(viewModel.uiState.value.bikes.isEmpty())
            assertEquals("Something went wrong. Try again.", viewModel.uiState.value.error)
        }

    @Test
    fun `removes eligible bike from browse list after confirmed delete succeeds`() =
        runTest {
            val commuter = bikeListItem(id = "bike-1", name = "Commuter")
            val trainer = bikeListItem(id = "bike-2", name = "Trainer")
            val repository =
                FakeBikeListRepository(
                    getBikesResults = mutableListOf(ApiResult.Success(listOf(commuter, trainer))),
                    deleteBikeResult = BikeDeleteResult.Success,
                )
            val viewModel =
                BikeListViewModel(
                    repository = repository,
                    sessionRepository = FakeSessionRepository(),
                    selectionMode = false,
                )

            viewModel.requestDelete(commuter)
            assertEquals("bike-1", viewModel.uiState.value.pendingDeleteBike?.id)

            viewModel.events.test {
                viewModel.confirmDelete()

                expectNoEvents()
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(listOf("bike-1"), repository.deletedBikeIds)
            assertEquals(listOf(trainer), viewModel.uiState.value.bikes)
            assertEquals(null, viewModel.uiState.value.pendingDeleteBike)
            assertEquals(null, viewModel.uiState.value.deletingBikeId)
        }

    @Test
    fun `refreshes bike row and explains conflict when repair history blocks delete`() =
        runTest {
            val initialBike =
                bikeListItem(
                    id = "bike-1",
                    name = "Commuter",
                    hasSessions = false,
                )
            val refreshedBike = initialBike.copy(hasSessions = true)
            val repository =
                FakeBikeListRepository(
                    getBikesResults =
                        mutableListOf(
                            ApiResult.Success(listOf(initialBike)),
                            ApiResult.Success(listOf(refreshedBike)),
                        ),
                    deleteBikeResult = BikeDeleteResult.SessionHistoryConflict,
                )
            val viewModel =
                BikeListViewModel(
                    repository = repository,
                    sessionRepository = FakeSessionRepository(),
                    selectionMode = false,
                )

            viewModel.requestDelete(initialBike)

            viewModel.events.test {
                viewModel.confirmDelete()

                assertEquals(
                    UiEvent.ShowSnackbar(BikeListViewModel.DELETE_SESSION_HISTORY_MESSAGE),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(2, repository.getBikesCalls)
            assertEquals(true, viewModel.uiState.value.bikes.single().hasSessions)
            assertEquals(null, viewModel.uiState.value.deletingBikeId)
            assertEquals(null, viewModel.uiState.value.pendingDeleteBike)
        }

    @Test
    fun `does not expose delete confirmation for bikes that already have repair history`() =
        runTest {
            val protectedBike =
                bikeListItem(
                    id = "bike-1",
                    name = "Commuter",
                    hasSessions = true,
                )
            val repository =
                FakeBikeListRepository(
                    getBikesResults = mutableListOf(ApiResult.Success(listOf(protectedBike))),
                )
            val viewModel =
                BikeListViewModel(
                    repository = repository,
                    sessionRepository = FakeSessionRepository(),
                    selectionMode = false,
                )

            viewModel.requestDelete(protectedBike)

            assertEquals(null, viewModel.uiState.value.pendingDeleteBike)
            assertTrue(repository.deletedBikeIds.isEmpty())
        }

    @Test
    fun `selection mode creates a new repair session when bike has no prior sessions`() =
        runTest {
            val bike = bikeListItem(id = "bike-1", name = "Commuter")
            val sessionRepository =
                FakeSessionRepository(
                    getBikeSessionsResult =
                        ApiResult.Success(BikeSessionListResponse(items = emptyList())),
                    createBikeSessionResult =
                        ApiResult.Success(
                            BikeSession(
                                workflow = "repair",
                                id = "ses_1",
                                bikeId = bike.id,
                                phase = "diagnostic",
                                status = "created",
                                createdAt = "2026-07-02T00:00:00Z",
                                updatedAt = "2026-07-02T00:00:00Z",
                            ),
                        ),
                )
            val viewModel =
                BikeListViewModel(
                    repository =
                        FakeBikeListRepository(
                            getBikesResults = mutableListOf(ApiResult.Success(listOf(bike))),
                        ),
                    sessionRepository = sessionRepository,
                    selectionMode = true,
                )

            viewModel.events.test {
                viewModel.selectBike(bike)

                assertEquals(
                    UiEvent.NavigateTo(AppRoute.DiagnosticChat.create("ses_1")),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(listOf("bike-1"), sessionRepository.getBikeSessionsBikeIds)
            assertEquals(listOf("repair"), sessionRepository.requestedWorkflows)
            assertEquals(listOf(BikeSessionCreate(workflow = "repair", bikeId = "bike-1")), sessionRepository.createdSessions)
            assertEquals(null, viewModel.uiState.value.selectedBikeId)
            assertFalse(viewModel.uiState.value.isLoadingBikeSessions)
            assertFalse(viewModel.uiState.value.isCreatingSession)
        }

    @Test
    fun `selection mode shows chooser when bike has prior sessions`() =
        runTest {
            val bike = bikeListItem(id = "bike-1", name = "Commuter")
            val newestSession =
                BikeSession(
                    workflow = "repair",
                    id = "ses_2",
                    bikeId = bike.id,
                    phase = "diagnostic",
                    status = "completed",
                    createdAt = "2026-07-02T01:00:00Z",
                    updatedAt = "2026-07-02T01:30:00Z",
                )
            val olderSession =
                BikeSession(
                    workflow = "repair",
                    id = "ses_1",
                    bikeId = bike.id,
                    phase = "diagnostic",
                    status = "created",
                    createdAt = "2026-07-01T01:00:00Z",
                    updatedAt = "2026-07-01T01:30:00Z",
                )
            val sessionRepository =
                FakeSessionRepository(
                    getBikeSessionsResult =
                        ApiResult.Success(
                            BikeSessionListResponse(items = listOf(newestSession, olderSession)),
                        ),
                )
            val viewModel =
                BikeListViewModel(
                    repository =
                        FakeBikeListRepository(
                            getBikesResults = mutableListOf(ApiResult.Success(listOf(bike))),
                        ),
                    sessionRepository = sessionRepository,
                    selectionMode = true,
                )

            viewModel.events.test {
                viewModel.selectBike(bike)

                expectNoEvents()
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(listOf("bike-1"), sessionRepository.getBikeSessionsBikeIds)
            assertEquals(listOf("repair"), sessionRepository.requestedWorkflows)
            assertTrue(viewModel.uiState.value.sessionChooser != null)
            assertEquals("bike-1", viewModel.uiState.value.sessionChooser?.bikeId)
            assertTrue(sessionRepository.createdSessions.isEmpty())
            assertEquals(null, viewModel.uiState.value.selectedBikeId)
            assertFalse(viewModel.uiState.value.isLoadingBikeSessions)
            assertFalse(viewModel.uiState.value.isCreatingSession)
        }

    @Test
    fun `chooser promotes newest resumable session and keeps older rows newest first`() =
        runTest {
            val bike = bikeListItem(id = "bike-1", name = "Commuter")
            val newestNonResumable =
                BikeSession(
                    workflow = "repair",
                    id = "ses_3",
                    bikeId = bike.id,
                    phase = "plan",
                    status = "created",
                    createdAt = "2026-07-03T01:00:00Z",
                    updatedAt = "2026-07-03T01:30:00Z",
                )
            val newestResumable =
                BikeSession(
                    workflow = "repair",
                    id = "ses_2",
                    bikeId = bike.id,
                    phase = "diagnostic",
                    status = "awaiting_user",
                    createdAt = "2026-07-02T01:00:00Z",
                    updatedAt = "2026-07-02T01:30:00Z",
                )
            val olderResumable =
                BikeSession(
                    workflow = "repair",
                    id = "ses_1",
                    bikeId = bike.id,
                    phase = "diagnostic",
                    status = "created",
                    createdAt = "2026-07-01T01:00:00Z",
                    updatedAt = "2026-07-01T01:30:00Z",
                )
            val sessionRepository =
                FakeSessionRepository(
                    getBikeSessionsResult =
                        ApiResult.Success(
                            BikeSessionListResponse(
                                items =
                                    listOf(
                                        newestNonResumable,
                                        newestResumable,
                                        olderResumable,
                                    ),
                            ),
                        ),
                )
            val viewModel =
                BikeListViewModel(
                    repository =
                        FakeBikeListRepository(
                            getBikesResults = mutableListOf(ApiResult.Success(listOf(bike))),
                        ),
                    sessionRepository = sessionRepository,
                    selectionMode = true,
                )

            viewModel.selectBike(bike)

            val chooser = viewModel.uiState.value.sessionChooser
            assertEquals("ses_2", chooser?.primaryResumeSession?.id)
            assertEquals("Awaiting your reply", chooser?.primaryResumeSession?.statusLabel)
            assertEquals(listOf("ses_3", "ses_1"), chooser?.olderSessions?.map { it.id })
            assertEquals(listOf(false, true), chooser?.olderSessions?.map { it.isResumable })
        }

    @Test
    fun `chooser navigates only for resumable sessions`() =
        runTest {
            val bike = bikeListItem(id = "bike-1", name = "Commuter")
            val readOnlySession =
                BikeSession(
                    workflow = "repair",
                    id = "ses_2",
                    bikeId = bike.id,
                    phase = "execution",
                    status = "running",
                    createdAt = "2026-07-02T01:00:00Z",
                    updatedAt = "2026-07-02T01:30:00Z",
                )
            val resumableSession =
                BikeSession(
                    workflow = "repair",
                    id = "ses_1",
                    bikeId = bike.id,
                    phase = "diagnostic",
                    status = "created",
                    createdAt = "2026-07-01T01:00:00Z",
                    updatedAt = "2026-07-01T01:30:00Z",
                )
            val viewModel =
                BikeListViewModel(
                    repository =
                        FakeBikeListRepository(
                            getBikesResults = mutableListOf(ApiResult.Success(listOf(bike))),
                        ),
                    sessionRepository =
                        FakeSessionRepository(
                            getBikeSessionsResult =
                                ApiResult.Success(
                                    BikeSessionListResponse(
                                        items = listOf(readOnlySession, resumableSession),
                                    ),
                                ),
                        ),
                    selectionMode = true,
                )

            viewModel.selectBike(bike)

            viewModel.events.test {
                viewModel.selectSessionFromChooser("ses_2")
                expectNoEvents()

                viewModel.selectSessionFromChooser("ses_1")
                assertEquals(
                    UiEvent.NavigateTo(AppRoute.DiagnosticChat.create("ses_1")),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }
        }

    @Test
    fun `starting new session from chooser requires confirmation when resumable history exists`() =
        runTest {
            val bike = bikeListItem(id = "bike-1", name = "Commuter")
            val resumableSession =
                BikeSession(
                    workflow = "repair",
                    id = "ses_1",
                    bikeId = bike.id,
                    phase = "diagnostic",
                    status = "awaiting_user",
                    createdAt = "2026-07-01T01:00:00Z",
                    updatedAt = "2026-07-01T01:30:00Z",
                )
            val sessionRepository =
                FakeSessionRepository(
                    getBikeSessionsResult =
                        ApiResult.Success(
                            BikeSessionListResponse(items = listOf(resumableSession)),
                        ),
                    createBikeSessionResult =
                        ApiResult.Success(
                            BikeSession(
                                workflow = "repair",
                                id = "ses_2",
                                bikeId = bike.id,
                                phase = "diagnostic",
                                status = "created",
                                createdAt = "2026-07-03T01:00:00Z",
                                updatedAt = "2026-07-03T01:00:00Z",
                            ),
                        ),
                )
            val viewModel =
                BikeListViewModel(
                    repository =
                        FakeBikeListRepository(
                            getBikesResults = mutableListOf(ApiResult.Success(listOf(bike))),
                        ),
                    sessionRepository = sessionRepository,
                    selectionMode = true,
                )

            viewModel.selectBike(bike)
            viewModel.requestStartNewSessionFromChooser()

            assertTrue(viewModel.uiState.value.showStartNewSessionConfirmation)
            assertTrue(sessionRepository.createdSessions.isEmpty())

            viewModel.events.test {
                viewModel.confirmStartNewSessionFromChooser()

                assertEquals(
                    UiEvent.NavigateTo(AppRoute.DiagnosticChat.create("ses_2")),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(listOf(BikeSessionCreate(workflow = "repair", bikeId = "bike-1")), sessionRepository.createdSessions)
            assertFalse(viewModel.uiState.value.showStartNewSessionConfirmation)
            assertEquals(null, viewModel.uiState.value.sessionChooser)
        }

    @Test
    fun `starting new session from chooser keeps chooser visible on create error`() =
        runTest {
            val bike = bikeListItem(id = "bike-1", name = "Commuter")
            val resumableSession =
                BikeSession(
                    workflow = "repair",
                    id = "ses_1",
                    bikeId = bike.id,
                    phase = "diagnostic",
                    status = "awaiting_user",
                    createdAt = "2026-07-01T01:00:00Z",
                    updatedAt = "2026-07-01T01:30:00Z",
                )
            val viewModel =
                BikeListViewModel(
                    repository =
                        FakeBikeListRepository(
                            getBikesResults = mutableListOf(ApiResult.Success(listOf(bike))),
                        ),
                    sessionRepository =
                        FakeSessionRepository(
                            getBikeSessionsResult =
                                ApiResult.Success(
                                    BikeSessionListResponse(items = listOf(resumableSession)),
                                ),
                            createBikeSessionResult =
                                ApiResult.Error(500, "Something went wrong. Try again."),
                        ),
                    selectionMode = true,
                )

            viewModel.selectBike(bike)
            viewModel.requestStartNewSessionFromChooser()

            viewModel.events.test {
                viewModel.confirmStartNewSessionFromChooser()

                assertEquals(
                    UiEvent.ShowSnackbar("Something went wrong. Try again."),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertTrue(viewModel.uiState.value.sessionChooser != null)
            assertFalse(viewModel.uiState.value.isCreatingSession)
            assertFalse(viewModel.uiState.value.showStartNewSessionConfirmation)
        }

    private class FakeBikeListRepository(
        private val getBikesResults: MutableList<ApiResult<List<BikeListItem>>>,
        private val deleteBikeResult: BikeDeleteResult = BikeDeleteResult.Success,
    ) : BikeListRepository {
        var getBikesCalls = 0
        val deletedBikeIds = mutableListOf<String>()

        override suspend fun getBikes(): ApiResult<List<BikeListItem>> {
            getBikesCalls += 1
            return getBikesResults.removeFirst()
        }

        override suspend fun deleteBike(bikeId: String): BikeDeleteResult {
            deletedBikeIds += bikeId
            return deleteBikeResult
        }
    }

    private class FakeSessionRepository(
        private val getBikeSessionsResult: ApiResult<BikeSessionListResponse> =
            ApiResult.Success(BikeSessionListResponse(items = emptyList())),
        private val createBikeSessionResult: ApiResult<BikeSession> =
            ApiResult.Error(500, "Something went wrong. Try again."),
    ) : SessionRepository {
        val getBikeSessionsBikeIds = mutableListOf<String>()
        val requestedWorkflows = mutableListOf<String>()
        val createdSessions = mutableListOf<BikeSessionCreate>()

        override suspend fun getBikeSessions(
            bikeId: String,
            workflow: String,
        ): ApiResult<BikeSessionListResponse> {
            getBikeSessionsBikeIds += bikeId
            requestedWorkflows += workflow
            return getBikeSessionsResult
        }

        override suspend fun createBikeSession(body: BikeSessionCreate): ApiResult<BikeSession> {
            createdSessions += body
            return createBikeSessionResult
        }

        override suspend fun getBikeSession(sessionId: String): ApiResult<BikeSession> {
            error("Not used in this test")
        }

        override suspend fun createTurn(
            sessionId: String,
            body: TurnCreate,
        ): ApiResult<TurnAccepted> {
            error("Not used in this test")
        }
    }

    private fun bikeListItem(
        id: String,
        name: String,
        hasSessions: Boolean = false,
    ) = BikeListItem(
        id = id,
        name = name,
        makeModelYear = "Trek Domane 2022",
        specificationSummary = "Shimano 105 2x11 • Hydraulic disc",
        hasSessions = hasSessions,
    )
}
