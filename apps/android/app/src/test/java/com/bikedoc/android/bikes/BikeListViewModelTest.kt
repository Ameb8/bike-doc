package com.bikedoc.android.bikes

import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.api.models.RepairSessionCreate
import com.bikedoc.android.api.models.RepairSessionListResponse
import com.bikedoc.android.api.models.TurnAccepted
import com.bikedoc.android.api.models.TurnCreate
import com.bikedoc.android.navigation.AppRoute
import com.bikedoc.android.navigation.UiEvent
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
                                        hasRepairSessions = true,
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
                    hasRepairSessions = false,
                )
            val refreshedBike = initialBike.copy(hasRepairSessions = true)
            val repository =
                FakeBikeListRepository(
                    getBikesResults =
                        mutableListOf(
                            ApiResult.Success(listOf(initialBike)),
                            ApiResult.Success(listOf(refreshedBike)),
                        ),
                    deleteBikeResult = BikeDeleteResult.RepairHistoryConflict,
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
                    UiEvent.ShowSnackbar(BikeListViewModel.DELETE_REPAIR_HISTORY_MESSAGE),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(2, repository.getBikesCalls)
            assertEquals(true, viewModel.uiState.value.bikes.single().hasRepairSessions)
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
                    hasRepairSessions = true,
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
                    getRepairSessionsResult =
                        ApiResult.Success(RepairSessionListResponse(items = emptyList())),
                    createRepairSessionResult =
                        ApiResult.Success(
                            RepairSession(
                                id = "session-1",
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
                    UiEvent.NavigateTo(AppRoute.DiagnosticChat.create("session-1")),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(listOf("bike-1"), sessionRepository.getRepairSessionsBikeIds)
            assertEquals(listOf(RepairSessionCreate(bikeId = "bike-1")), sessionRepository.createdSessions)
            assertEquals(null, viewModel.uiState.value.selectedBikeId)
            assertFalse(viewModel.uiState.value.isLoadingBikeSessions)
            assertFalse(viewModel.uiState.value.isCreatingSession)
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
        private val getRepairSessionsResult: ApiResult<RepairSessionListResponse> =
            ApiResult.Success(RepairSessionListResponse(items = emptyList())),
        private val createRepairSessionResult: ApiResult<RepairSession> =
            ApiResult.Error(500, "Something went wrong. Try again."),
    ) : SessionRepository {
        val getRepairSessionsBikeIds = mutableListOf<String>()
        val createdSessions = mutableListOf<RepairSessionCreate>()

        override suspend fun getRepairSessions(bikeId: String): ApiResult<RepairSessionListResponse> {
            getRepairSessionsBikeIds += bikeId
            return getRepairSessionsResult
        }

        override suspend fun createRepairSession(body: RepairSessionCreate): ApiResult<RepairSession> {
            createdSessions += body
            return createRepairSessionResult
        }

        override suspend fun getRepairSession(
            sessionId: String,
        ): ApiResult<RepairSession> = error("Not used in this test")

        override suspend fun createTurn(
            sessionId: String,
            body: TurnCreate,
        ): ApiResult<TurnAccepted> = error("Not used in this test")
    }

    private fun bikeListItem(
        id: String,
        name: String,
        hasRepairSessions: Boolean = false,
    ) = BikeListItem(
        id = id,
        name = name,
        makeModelYear = "Trek Domane 2022",
        specificationSummary = "Shimano 105 2x11 • Hydraulic disc",
        hasRepairSessions = hasRepairSessions,
    )
}
