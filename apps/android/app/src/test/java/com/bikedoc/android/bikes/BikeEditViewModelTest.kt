package com.bikedoc.android.bikes

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.BikeRepository
import com.bikedoc.android.api.models.Bike
import com.bikedoc.android.api.models.BikeCreate
import com.bikedoc.android.api.models.BikeListResponse
import com.bikedoc.android.api.models.BikePatch
import com.bikedoc.android.navigation.UiEvent
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class BikeEditViewModelTest {
    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `create bike requires display name before saving`() =
        runTest {
            val repository = FakeBikeRepository()
            val viewModel = BikeEditViewModel(repository, SavedStateHandle())

            viewModel.onDisplayNameChanged("   ")

            viewModel.save()

            assertEquals(
                "Display name is required.",
                viewModel.uiState.value.validationErrors["displayName"],
            )
            assertFalse(viewModel.uiState.value.isSaving)
            assertNull(repository.createdBike)
        }

    @Test
    fun `create bike validates model year range before saving`() =
        runTest {
            val repository = FakeBikeRepository()
            val viewModel = BikeEditViewModel(repository, SavedStateHandle())

            viewModel.onDisplayNameChanged("Daily Rider")
            viewModel.onModelYearChanged("1700")

            viewModel.save()

            assertEquals(
                "Model year must be between 1880 and 2100.",
                viewModel.uiState.value.validationErrors["modelYear"],
            )
            assertNull(repository.createdBike)
        }

    @Test
    fun `create bike posts canonical bike payload and navigates back on success`() =
        runTest {
            val repository = FakeBikeRepository()
            val viewModel = BikeEditViewModel(repository, SavedStateHandle())

            viewModel.onDisplayNameChanged("Daily Rider")
            viewModel.onMakeChanged("Trek")
            viewModel.onModelChanged("Domane")
            viewModel.onModelYearChanged("2022")
            viewModel.onBikeTypeChanged("road")
            viewModel.onFrameMaterialChanged("carbon")
            viewModel.onDrivetrainChanged("Shimano 105 2x11")
            viewModel.onBrakeTypeChanged("hydraulic_disc")
            viewModel.onWheelSizeChanged("700c")
            viewModel.onTireSizeChanged("32mm")
            viewModel.onNotesChanged("Fit checked")

            viewModel.events.test {
                viewModel.save()

                assertEquals(
                    UiEvent.NavigateBackWithResult(BIKE_LIST_REFRESH_REQUESTED, true),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(
                BikeCreate(
                    displayName = "Daily Rider",
                    make = "Trek",
                    model = "Domane",
                    modelYear = 2022,
                    bikeType = "road",
                    frameMaterial = "carbon",
                    drivetrain = "Shimano 105 2x11",
                    brakeType = "hydraulic_disc",
                    wheelSize = "700c",
                    tireSize = "32mm",
                    notes = "Fit checked",
                ),
                repository.createdBike,
            )
            assertFalse(viewModel.uiState.value.isSaving)
            assertTrue(viewModel.uiState.value.validationErrors.isEmpty())
        }

    @Test
    fun `edit bike loads existing bike into the shared form`() =
        runTest {
            val repository =
                FakeBikeRepository(
                    bikeResult =
                        ApiResult.Success(
                            bike(
                                id = "bike-123",
                                displayName = "Rain Bike",
                                make = "Surly",
                                model = "Midnight Special",
                                modelYear = 2021,
                                bikeType = "road",
                                frameMaterial = "steel",
                                drivetrain = "SRAM Rival",
                                brakeType = "mechanical_disc",
                                wheelSize = "700c",
                                tireSize = "35mm",
                                notes = "Needs fresh pads",
                            ),
                        ),
                )

            val viewModel = BikeEditViewModel(repository, SavedStateHandle(mapOf("bikeId" to "bike-123")))

            assertEquals(1, repository.getBikeCalls)
            assertFalse(viewModel.uiState.value.isNew)
            assertFalse(viewModel.uiState.value.isLoading)
            assertEquals("Rain Bike", viewModel.uiState.value.displayName)
            assertEquals("Surly", viewModel.uiState.value.make)
            assertEquals("2021", viewModel.uiState.value.modelYear)
            assertEquals("mechanical_disc", viewModel.uiState.value.brakeType)
        }

    @Test
    fun `edit bike patches the existing bike and navigates back on success`() =
        runTest {
            val repository =
                FakeBikeRepository(
                    bikeResult = ApiResult.Success(bike(id = "bike-123", displayName = "Rain Bike")),
                )
            val viewModel = BikeEditViewModel(repository, SavedStateHandle(mapOf("bikeId" to "bike-123")))

            viewModel.onDisplayNameChanged("Rain Bike Mk II")
            viewModel.onModelYearChanged("2024")
            viewModel.onBrakeTypeChanged("hydraulic_disc")

            viewModel.events.test {
                viewModel.save()

                assertEquals(
                    UiEvent.NavigateBackWithResult(BIKE_LIST_REFRESH_REQUESTED, true),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals("bike-123", repository.updatedBikeId)
            assertEquals(
                BikePatch(
                    displayName = "Rain Bike Mk II",
                    make = null,
                    model = null,
                    modelYear = 2024,
                    bikeType = "unknown",
                    frameMaterial = "unknown",
                    drivetrain = null,
                    brakeType = "hydraulic_disc",
                    wheelSize = null,
                    tireSize = null,
                    notes = null,
                ),
                repository.updatedBike,
            )
        }

    @Test
    fun `restores unsaved form state from saved state handle`() =
        runTest {
            val repository = FakeBikeRepository()
            val viewModel =
                BikeEditViewModel(
                    repository,
                    SavedStateHandle(
                        mapOf(
                            "displayName" to "Trainer Bike",
                            "modelYear" to "2020",
                            "bikeType" to "gravel",
                            "notes" to "Indoor setup",
                        ),
                    ),
                )

            assertEquals("Trainer Bike", viewModel.uiState.value.displayName)
            assertEquals("2020", viewModel.uiState.value.modelYear)
            assertEquals("gravel", viewModel.uiState.value.bikeType)
            assertEquals("Indoor setup", viewModel.uiState.value.notes)
        }

    private class FakeBikeRepository(
        private val bikeResult: ApiResult<Bike> = ApiResult.Success(bike(id = "created-bike", displayName = "Created")),
        private val createResult: ApiResult<Bike> = ApiResult.Success(bike(id = "created-bike", displayName = "Created")),
        private val updateResult: ApiResult<Bike> = ApiResult.Success(bike(id = "updated-bike", displayName = "Updated")),
    ) : BikeRepository {
        var getBikeCalls = 0
        var createdBike: BikeCreate? = null
        var updatedBikeId: String? = null
        var updatedBike: BikePatch? = null

        override suspend fun getBikes(): ApiResult<BikeListResponse> {
            error("Unused in BikeEditViewModel tests")
        }

        override suspend fun createBike(bike: BikeCreate): ApiResult<Bike> {
            createdBike = bike
            return createResult
        }

        override suspend fun getBike(bikeId: String): ApiResult<Bike> {
            getBikeCalls += 1
            return bikeResult
        }

        override suspend fun updateBike(
            bikeId: String,
            bike: BikePatch,
        ): ApiResult<Bike> {
            updatedBikeId = bikeId
            updatedBike = bike
            return updateResult
        }

        override suspend fun deleteBike(bikeId: String): ApiResult<Unit> {
            error("Unused in BikeEditViewModel tests")
        }
    }
}

private fun bike(
    id: String,
    displayName: String,
    make: String? = null,
    model: String? = null,
    modelYear: Int? = null,
    bikeType: String = "unknown",
    frameMaterial: String? = null,
    drivetrain: String? = null,
    brakeType: String? = null,
    wheelSize: String? = null,
    tireSize: String? = null,
    notes: String? = null,
): Bike =
    Bike(
        id = id,
        userId = "user-1",
        displayName = displayName,
        hasRepairSessions = false,
        make = make,
        model = model,
        modelYear = modelYear,
        bikeType = bikeType,
        frameMaterial = frameMaterial,
        drivetrain = drivetrain,
        brakeType = brakeType,
        wheelSize = wheelSize,
        tireSize = tireSize,
        notes = notes,
        createdAt = "2026-06-28T12:00:00Z",
        updatedAt = "2026-06-28T12:00:00Z",
    )
