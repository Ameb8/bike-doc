package com.bikedoc.android.home

import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.auth.AuthFailureReason
import com.bikedoc.android.auth.AuthProvider
import com.bikedoc.android.auth.AuthResult
import com.bikedoc.android.auth.GoogleSignInHost
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

    private class FakeHomeRepository(
        private val result: ApiResult<BikeDocUser>,
    ) : HomeRepository {
        override suspend fun getCurrentUser(): ApiResult<BikeDocUser> = result
    }

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

        override fun currentUserId(): String? = if (signedIn) "user-1" else null

        override fun isSignedIn(): Boolean = signedIn

        override fun signOut() {
            signOutCalled = true
        }
    }
}
