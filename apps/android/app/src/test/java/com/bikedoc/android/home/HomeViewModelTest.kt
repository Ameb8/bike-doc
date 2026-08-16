package com.bikedoc.android.home

import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.auth.AuthFailureReason
import com.bikedoc.android.auth.AuthProvider
import com.bikedoc.android.auth.AuthResult
import com.bikedoc.android.auth.GoogleSignInHost
import com.bikedoc.android.auth.PendingAuthCredential
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
class HomeViewModelTest {
    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `loads signed in user profile on init`() =
        runTest {
            val viewModel =
                HomeViewModel(
                    authProvider = FakeAuthProvider(signedIn = true),
                    homeRepository =
                        FakeHomeRepository(
                            result = ApiResult.Success(BikeDocUser(id = "user-1", displayName = "Alex")),
                        ),
                )

            assertEquals("Alex", viewModel.uiState.value.displayName)
            assertFalse(viewModel.uiState.value.isLoading)
            assertEquals(null, viewModel.uiState.value.error)
        }

    @Test
    fun `signs out and redirects when profile load returns unauthorized`() =
        runTest {
            val authProvider = FakeAuthProvider(signedIn = true)
            val viewModel =
                HomeViewModel(
                    authProvider = authProvider,
                    homeRepository = FakeHomeRepository(result = ApiResult.Error(401, "Session expired.")),
                )

            viewModel.events.test {
                assertEquals(UiEvent.NavigateTo(AppRoute.Auth.route), awaitItem())
                cancelAndIgnoreRemainingEvents()
            }
            assertTrue(authProvider.signOutCalled)
        }

    @Test
    fun `starts repair for the selected bike`() =
        runTest {
            val repository =
                FakeHomeRepository(
                    result = ApiResult.Success(BikeDocUser(id = "user-1", displayName = "Alex")),
                    bikesResult = ApiResult.Success(listOf(HomeBike(id = "bike-1", name = "Daily Rider"))),
                    startRepairResult = ApiResult.Success(repairSession()),
                )
            val viewModel = HomeViewModel(FakeAuthProvider(signedIn = true), repository)

            viewModel.selectRepairBike("bike-1")
            viewModel.events.test {
                viewModel.startRepair()

                assertEquals(
                    UiEvent.NavigateTo(AppRoute.DiagnosticChat.create("session-1")),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals("bike-1", repository.startedBikeId)
        }

    @Test
    fun `opens the selected bike profile editor`() =
        runTest {
            val repository =
                FakeHomeRepository(
                    result = ApiResult.Success(BikeDocUser(id = "user-1", displayName = "Alex")),
                    bikesResult = ApiResult.Success(listOf(HomeBike(id = "bike-1", name = "Daily Rider"))),
                )
            val viewModel = HomeViewModel(FakeAuthProvider(signedIn = true), repository)

            viewModel.events.test {
                viewModel.openSelectedBikeProfile()

                assertEquals(UiEvent.NavigateTo(AppRoute.BikeEdit.create("bike-1")), awaitItem())
                cancelAndIgnoreRemainingEvents()
            }
        }

    @Test
    fun `expands and closes the session setup`() =
        runTest {
            val viewModel =
                HomeViewModel(
                    authProvider = FakeAuthProvider(signedIn = true),
                    homeRepository =
                        FakeHomeRepository(
                            result = ApiResult.Success(BikeDocUser(id = "user-1", displayName = "Alex")),
                        ),
                )

            viewModel.startSetup()
            assertTrue(viewModel.uiState.value.isSetupExpanded)

            viewModel.closeSetup()
            assertFalse(viewModel.uiState.value.isSetupExpanded)
        }

    private class FakeHomeRepository(
        private val result: ApiResult<BikeDocUser>,
        private val bikesResult: ApiResult<List<HomeBike>> = ApiResult.Success(emptyList()),
        private val startRepairResult: ApiResult<RepairSession> = ApiResult.Error(500, "Unable to start repair."),
    ) : HomeRepository {
        var startedBikeId: String? = null

        override suspend fun getCurrentUser(): ApiResult<BikeDocUser> = result

        override suspend fun getBikes(): ApiResult<List<HomeBike>> = bikesResult

        override suspend fun startRepair(bikeId: String?): ApiResult<RepairSession> {
            startedBikeId = bikeId
            return startRepairResult
        }
    }

    private fun repairSession() =
        RepairSession(
            id = "session-1",
            bikeId = "bike-1",
            phase = "diagnostic",
            status = "created",
            createdAt = "2026-01-01T00:00:00Z",
            updatedAt = "2026-01-01T00:00:00Z",
        )

    private class FakeAuthProvider(
        private val signedIn: Boolean,
    ) : AuthProvider {
        var signOutCalled = false

        override suspend fun getToken(forceRefresh: Boolean): String = "token"

        override suspend fun signIn(
            email: String,
            password: String,
        ): AuthResult = AuthResult.Failure(AuthFailureReason.Unknown)

        override suspend fun createAccount(
            email: String,
            password: String,
        ): AuthResult = AuthResult.Failure(AuthFailureReason.Unknown)

        override suspend fun continueWithGoogle(host: GoogleSignInHost): AuthResult {
            return AuthResult.Failure(AuthFailureReason.Unknown)
        }

        override suspend fun linkWithGoogle(pendingCredential: PendingAuthCredential): AuthResult {
            return AuthResult.Failure(AuthFailureReason.Unknown)
        }

        override fun currentUserId(): String? = if (signedIn) "user-1" else null

        override fun currentUserEmail(): String? = null

        override fun isSignedIn(): Boolean = signedIn

        override fun signOut() {
            signOutCalled = true
        }
    }
}
