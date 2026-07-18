@file:Suppress("MaxLineLength")

package com.bikedoc.android.bikes

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.BikeRepository
import com.bikedoc.android.navigation.UiEvent
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class BikeEditViewModelTest {
    @get:Rule val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `create requires a display name`() =
        runTest {
            val repository = FakeBikeRepository()
            val viewModel = BikeEditViewModel(repository, SavedStateHandle())

            viewModel.save()

            assertEquals("Display name is required.", viewModel.uiState.value.validationErrors["displayName"])
            assertNull(repository.createdBike)
        }

    @Test
    fun `create posts structured V2 profile`() =
        runTest {
            val repository = FakeBikeRepository()
            val viewModel = BikeEditViewModel(repository, SavedStateHandle())
            val profile =
                BikeProfileEdit(
                    displayName = "Daily Rider",
                    identity = BikeIdentity(make = "Trek", model = "Domane", modelYear = 2022, bikeType = "road"),
                    frame = BikeFrame(material = "carbon", sizeLabel = "54 cm"),
                    brakes =
                        BikeBrakes(
                            BrakeAssembly(mechanism = "disc", actuation = "hydraulic"),
                            BrakeAssembly(mechanism = "disc", actuation = "hydraulic"),
                        ),
                    rollingSystem =
                        BikeRollingSystem(
                            WheelPosition(tire = TireComponent(markedSize = "700x32")),
                            WheelPosition(tire = TireComponent(markedSize = "700x32")),
                        ),
                    notes = "Fit checked",
                )
            viewModel.onProfileChanged(profile)

            viewModel.events.test {
                viewModel.save()
                assertEquals(UiEvent.NavigateBackWithResult(BIKE_LIST_REFRESH_REQUESTED, true), awaitItem())
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(profile, repository.createdBike)
            assertFalse(viewModel.uiState.value.isSaving)
        }

    @Test
    fun `edit loads V2 values rather than deprecated summaries`() =
        runTest {
            val profile =
                bike(
                    identity = BikeIdentity(make = "Surly", model = "Midnight Special", modelYear = 2021),
                    brakes = BikeBrakes(BrakeAssembly(mechanism = "rim_v_brake"), BrakeAssembly(mechanism = "disc")),
                    rollingSystem =
                        BikeRollingSystem(
                            WheelPosition(tire = TireComponent(markedSize = "700x32")),
                            WheelPosition(tire = TireComponent(markedSize = "650bx47")),
                        ),
                )
            val repository = FakeBikeRepository(bikeResult = ApiResult.Success(profile))
            val viewModel = BikeEditViewModel(repository, SavedStateHandle(mapOf("bikeId" to "bike-123")))

            assertEquals("Surly", viewModel.uiState.value.profile.identity.make)
            assertEquals("disc", viewModel.uiState.value.profile.brakes.rear.mechanism)
            assertEquals("650bx47", viewModel.uiState.value.profile.rollingSystem.rear.tire?.markedSize)
        }

    @Test
    fun `edit sends structured patch`() =
        runTest {
            val repository = FakeBikeRepository(bikeResult = ApiResult.Success(bike()))
            val viewModel = BikeEditViewModel(repository, SavedStateHandle(mapOf("bikeId" to "bike-123")))
            val updated =
                viewModel.uiState.value.profile.copy(
                    brakes = BikeBrakes(BrakeAssembly(mechanism = "disc"), BrakeAssembly(mechanism = "disc", actuation = "hydraulic")),
                )
            viewModel.onProfileChanged(updated)

            viewModel.events.test {
                viewModel.save()
                assertEquals(UiEvent.NavigateBackWithResult(BIKE_LIST_REFRESH_REQUESTED, true), awaitItem())
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals("bike-123", repository.updatedBikeId)
            assertEquals(updated, repository.updatedBike)
        }

    private class FakeBikeRepository(
        private val bikeResult: ApiResult<BikeProfile> = ApiResult.Success(bike()),
    ) : BikeRepository {
        var createdBike: BikeProfileEdit? = null
        var updatedBikeId: String? = null
        var updatedBike: BikeProfileEdit? = null

        override suspend fun getBikes(): ApiResult<List<BikeProfile>> = error("Unused")

        override suspend fun createBike(bike: BikeProfileEdit): ApiResult<BikeProfile> {
            createdBike = bike
            return ApiResult.Success(bike())
        }

        override suspend fun getBike(bikeId: String): ApiResult<BikeProfile> = bikeResult

        override suspend fun updateBike(
            bikeId: String,
            bike: BikeProfileEdit,
        ): ApiResult<BikeProfile> {
            updatedBikeId = bikeId
            updatedBike = bike
            return ApiResult.Success(bike())
        }

        override suspend fun deleteBike(bikeId: String): ApiResult<Unit> = error("Unused")
    }
}

private fun bike(
    identity: BikeIdentity = BikeIdentity(),
    brakes: BikeBrakes = BikeBrakes(BrakeAssembly(), BrakeAssembly()),
    rollingSystem: BikeRollingSystem = BikeRollingSystem(WheelPosition(), WheelPosition()),
) = BikeProfile(
    id = "bike-123", userId = "user-1", displayName = "Rain Bike", hasRepairSessions = false,
    schemaVersion = "bike_profile.v2", profileRevision = 0, identity = identity, frame = BikeFrame(), brakes = brakes,
    drivetrain = BikeDrivetrain(), rollingSystem = rollingSystem, suspension = BikeSuspension(), cockpit = BikeCockpit(),
    seating = BikeSeating(), electricAssist = BikeElectricAssist(), legacy = LegacyBikePresentation(bikeType = "unknown", notes = "Notes"),
    createdAt = "2026-06-28T12:00:00Z", updatedAt = "2026-06-28T12:00:00Z",
)
