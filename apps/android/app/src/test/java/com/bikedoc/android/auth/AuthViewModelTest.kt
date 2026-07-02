package com.bikedoc.android.auth

import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
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
class AuthViewModelTest {
    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `create account with mismatched passwords shows validation error`() =
        runTest {
            val authProvider = FakeAuthProvider()
            val viewModel = AuthViewModel(authProvider)

            viewModel.onModeSelected(AuthMode.CreateAccount)
            viewModel.onEmailChanged("rider@example.com")
            viewModel.onPasswordChanged("secret1")
            viewModel.onConfirmPasswordChanged("secret2")

            viewModel.submit()

            assertEquals(
                "Passwords do not match.",
                viewModel.uiState.value.validationErrors["confirmPassword"],
            )
            assertFalse(viewModel.uiState.value.isLoading)
            assertFalse(authProvider.createAccountCalled)
        }

    @Test
    fun `successful sign in navigates to home`() =
        runTest {
            val authProvider = FakeAuthProvider(signInResult = AuthResult.Success)
            val viewModel = AuthViewModel(authProvider)

            viewModel.onEmailChanged("rider@example.com")
            viewModel.onPasswordChanged("secret1")

            viewModel.events.test {
                viewModel.submit()

                assertEquals(
                    UiEvent.NavigateTo(AppRoute.Home.route),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }
        }

    @Test
    fun `create account maps auth failure to plain language error`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    createAccountResult = AuthResult.Failure(AuthFailureReason.EmailAlreadyInUse),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.onModeSelected(AuthMode.CreateAccount)
            viewModel.onEmailChanged("rider@example.com")
            viewModel.onPasswordChanged("secret1")
            viewModel.onConfirmPasswordChanged("secret1")

            viewModel.submit()

            assertEquals(
                "An account with this email already exists.",
                viewModel.uiState.value.error,
            )
            assertTrue(authProvider.createAccountCalled)
        }

    private class FakeAuthProvider(
        private val signInResult: AuthResult = AuthResult.Success,
        private val createAccountResult: AuthResult = AuthResult.Success,
    ) : AuthProvider {
        var createAccountCalled = false

        override suspend fun getToken(forceRefresh: Boolean): String = "token"

        override suspend fun signIn(
            email: String,
            password: String,
        ): AuthResult = signInResult

        override suspend fun createAccount(
            email: String,
            password: String,
        ): AuthResult {
            createAccountCalled = true
            return createAccountResult
        }

        override fun currentUserId(): String? = null

        override fun isSignedIn(): Boolean = false

        override fun signOut() = Unit
    }
}
