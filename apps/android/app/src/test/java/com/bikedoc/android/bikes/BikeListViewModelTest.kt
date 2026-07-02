package com.bikedoc.android.bikes

import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
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

            val viewModel = BikeListViewModel(repository = repository, selectionMode = false)

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
            val viewModel = BikeListViewModel(repository = repository, selectionMode = false)

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
            val viewModel = BikeListViewModel(repository = repository, selectionMode = false)

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
            val viewModel = BikeListViewModel(repository = repository, selectionMode = false)

            viewModel.requestDelete(protectedBike)

            assertEquals(null, viewModel.uiState.value.pendingDeleteBike)
            assertTrue(repository.deletedBikeIds.isEmpty())
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
