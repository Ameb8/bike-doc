package com.bikedoc.android.bikes

import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
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
                    result =
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
                    repository = FakeBikeListRepository(result = ApiResult.Success(emptyList())),
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
                            result = ApiResult.Error(500, "Something went wrong. Try again."),
                        ),
                    selectionMode = false,
                )

            assertFalse(viewModel.uiState.value.isLoading)
            assertTrue(viewModel.uiState.value.bikes.isEmpty())
            assertEquals("Something went wrong. Try again.", viewModel.uiState.value.error)
        }

    private class FakeBikeListRepository(
        private val result: ApiResult<List<BikeListItem>>,
    ) : BikeListRepository {
        var getBikesCalls = 0

        override suspend fun getBikes(): ApiResult<List<BikeListItem>> {
            getBikesCalls += 1
            return result
        }
    }
}
