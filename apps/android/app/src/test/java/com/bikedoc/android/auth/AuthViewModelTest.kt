package com.bikedoc.android.auth

import app.cash.turbine.test
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.navigation.AppRoute
import com.bikedoc.android.navigation.UiEvent
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runCurrent
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
    fun `create account with mismatched passwords shows validation message reason`() =
        runTest {
            val authProvider = FakeAuthProvider()
            val viewModel = AuthViewModel(authProvider)

            viewModel.onModeSelected(AuthMode.CreateAccount)
            viewModel.onEmailChanged("rider@example.com")
            viewModel.onPasswordChanged("secret1")
            viewModel.onConfirmPasswordChanged("secret2")

            viewModel.submit()

            assertEquals(
                AuthMessage.PasswordsDoNotMatch,
                viewModel.uiState.value.validationMessages[AuthField.ConfirmPassword],
            )
            assertEquals(null, viewModel.uiState.value.activeOperation)
            assertFalse(authProvider.createAccountCalled)
        }

    @Test
    fun `create account validation reports all invalid fields as message reasons`() =
        runTest {
            val authProvider = FakeAuthProvider()
            val viewModel = AuthViewModel(authProvider)

            viewModel.onModeSelected(AuthMode.CreateAccount)
            viewModel.submit()

            assertEquals(
                mapOf(
                    AuthField.Email to AuthMessage.EmailRequired,
                    AuthField.Password to AuthMessage.PasswordTooShort,
                ),
                viewModel.uiState.value.validationMessages,
            )
            assertEquals(null, viewModel.uiState.value.activeOperation)
            assertFalse(authProvider.createAccountCalled)
        }

    @Test
    fun `email password sign in is the active operation while submit is running`() =
        runTest {
            val signInResult = CompletableDeferred<AuthResult>()
            val authProvider = FakeAuthProvider(signInResult = signInResult)
            val viewModel = AuthViewModel(authProvider)

            viewModel.onEmailChanged("rider@example.com")
            viewModel.onPasswordChanged("secret1")
            viewModel.submit()
            runCurrent()

            assertEquals(AuthOperation.EmailPassword, viewModel.uiState.value.activeOperation)

            signInResult.complete(AuthResult.Success)
            runCurrent()

            assertEquals(null, viewModel.uiState.value.activeOperation)
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
    fun `sign in maps auth failure to message reason`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    signInResult = AuthResult.Failure(AuthFailureReason.InvalidCredentials),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.onEmailChanged("rider@example.com")
            viewModel.onPasswordChanged("wrong-password")
            viewModel.submit()

            assertEquals(AuthMessage.InvalidCredentials, viewModel.uiState.value.message)
            assertTrue(authProvider.signInCalled)
        }

    @Test
    fun `successful create account navigates to home`() =
        runTest {
            val authProvider = FakeAuthProvider(createAccountResult = AuthResult.Success)
            val viewModel = AuthViewModel(authProvider)

            viewModel.onModeSelected(AuthMode.CreateAccount)
            viewModel.onEmailChanged("rider@example.com")
            viewModel.onPasswordChanged("secret1")
            viewModel.onConfirmPasswordChanged("secret1")

            viewModel.events.test {
                viewModel.submit()

                assertEquals(
                    UiEvent.NavigateTo(AppRoute.Home.route),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }
            assertTrue(authProvider.createAccountCalled)
        }

    @Test
    fun `create account maps auth failure to message reason`() =
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
                AuthMessage.EmailAlreadyInUse,
                viewModel.uiState.value.message,
            )
            assertTrue(authProvider.createAccountCalled)
        }

    private class FakeAuthProvider(
        private val signInResult: Any = AuthResult.Success,
        private val createAccountResult: AuthResult = AuthResult.Success,
    ) : AuthProvider {
        var signInCalled = false
        var createAccountCalled = false

        override suspend fun getToken(forceRefresh: Boolean): String = "token"

        override suspend fun signIn(
            email: String,
            password: String,
        ): AuthResult {
            signInCalled = true
            return when (signInResult) {
                is CompletableDeferred<*> -> signInResult.await() as AuthResult
                is AuthResult -> signInResult
                else -> error("Unsupported sign-in result type.")
            }
        }

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
