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
    fun `successful Google sign in navigates to home`() =
        runTest {
            val authProvider = FakeAuthProvider(googleSignInResult = AuthResult.Success)
            val viewModel = AuthViewModel(authProvider)

            viewModel.events.test {
                viewModel.continueWithGoogle(FakeGoogleSignInHost)

                assertEquals(
                    UiEvent.NavigateTo(AppRoute.Home.route),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }
            assertTrue(authProvider.googleSignInCalled)
        }

    @Test
    fun `Google picker cancellation is silent`() =
        runTest {
            val authProvider = FakeAuthProvider(googleSignInResult = AuthResult.Cancelled)
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)
            runCurrent()

            assertEquals(null, viewModel.uiState.value.activeOperation)
            assertEquals(null, viewModel.uiState.value.message)
            assertTrue(authProvider.googleSignInCalled)
        }

    @Test
    fun `Google sign in maps provider failure to message reason`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult = AuthResult.Failure(AuthFailureReason.NoGoogleCredential),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)

            assertEquals(AuthMessage.NoGoogleCredential, viewModel.uiState.value.message)
            assertTrue(authProvider.googleSignInCalled)
        }

    @Test
    fun `Google link required from sign in switches to sign in and exposes linking state`() =
        runTest {
            val pendingCredential = FakePendingAuthCredential
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = "rider@example.com",
                            pendingCredential = pendingCredential,
                        ),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)

            assertEquals(AuthMode.SignIn, viewModel.uiState.value.mode)
            assertEquals("rider@example.com", viewModel.uiState.value.email)
            assertEquals(true, viewModel.uiState.value.isLinkingGoogle)
            assertEquals(AuthMessage.GoogleLinkRequired, viewModel.uiState.value.message)
            assertEquals(null, viewModel.uiState.value.activeOperation)
            assertTrue(authProvider.googleSignInCalled)
        }

    @Test
    fun `Google link required from create account switches to sign in and prefills email`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = "rider@example.com",
                            pendingCredential = FakePendingAuthCredential,
                        ),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.onModeSelected(AuthMode.CreateAccount)
            viewModel.onEmailChanged("typed@example.com")
            viewModel.onPasswordChanged("secret1")
            viewModel.onConfirmPasswordChanged("secret1")
            viewModel.continueWithGoogle(FakeGoogleSignInHost)

            assertEquals(AuthMode.SignIn, viewModel.uiState.value.mode)
            assertEquals("rider@example.com", viewModel.uiState.value.email)
            assertEquals("", viewModel.uiState.value.confirmPassword)
            assertEquals(true, viewModel.uiState.value.isLinkingGoogle)
            assertEquals(AuthMessage.GoogleLinkRequired, viewModel.uiState.value.message)
        }

    @Test
    fun `Google link required without email keeps typed email in linking state`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = null,
                            pendingCredential = FakePendingAuthCredential,
                        ),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.onEmailChanged("typed@example.com")
            viewModel.continueWithGoogle(FakeGoogleSignInHost)

            assertEquals(AuthMode.SignIn, viewModel.uiState.value.mode)
            assertEquals("typed@example.com", viewModel.uiState.value.email)
            assertEquals(true, viewModel.uiState.value.isLinkingGoogle)
            assertEquals(AuthMessage.GoogleLinkRequired, viewModel.uiState.value.message)
        }

    @Test
    fun `cancelling Google linking clears pending state and restores sign in`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = "rider@example.com",
                            pendingCredential = FakePendingAuthCredential,
                        ),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)
            viewModel.cancelGoogleLinking()

            assertEquals(AuthMode.SignIn, viewModel.uiState.value.mode)
            assertEquals(false, viewModel.uiState.value.isLinkingGoogle)
            assertEquals(null, viewModel.uiState.value.linkingGoogleEmail)
            assertEquals(null, viewModel.uiState.value.message)

            authProvider.googleSignInResult = AuthResult.Success
            viewModel.continueWithGoogle(FakeGoogleSignInHost)

            assertEquals(2, authProvider.googleSignInCallCount)
        }

    @Test
    fun `Google linking survives password and email edits until submit`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = "rider@example.com",
                            pendingCredential = FakePendingAuthCredential,
                        ),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)
            viewModel.onPasswordChanged("secret1")
            viewModel.onEmailChanged("other@example.com")

            assertEquals(true, viewModel.uiState.value.isLinkingGoogle)
            assertEquals("rider@example.com", viewModel.uiState.value.linkingGoogleEmail)
        }

    @Test
    fun `switching to create account clears pending Google linking`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = "rider@example.com",
                            pendingCredential = FakePendingAuthCredential,
                        ),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)
            viewModel.onModeSelected(AuthMode.CreateAccount)

            assertEquals(AuthMode.CreateAccount, viewModel.uiState.value.mode)
            assertEquals(false, viewModel.uiState.value.isLinkingGoogle)
            assertEquals(null, viewModel.uiState.value.linkingGoogleEmail)
            assertEquals(null, viewModel.uiState.value.message)
        }

    @Test
    fun `starting Google sign in again clears pending Google linking`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = "rider@example.com",
                            pendingCredential = FakePendingAuthCredential,
                        ),
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)

            authProvider.googleSignInResult = AuthResult.Cancelled
            viewModel.continueWithGoogle(FakeGoogleSignInHost)

            assertEquals(false, viewModel.uiState.value.isLinkingGoogle)
            assertEquals(null, viewModel.uiState.value.linkingGoogleEmail)
            assertEquals(null, viewModel.uiState.value.message)
        }

    @Test
    fun `successful sign in during Google linking links pending credential and navigates home`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = "rider@example.com",
                            pendingCredential = FakePendingAuthCredential,
                        ),
                    signedInEmail = "rider@example.com",
                    linkGoogleResult = AuthResult.Success,
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)
            viewModel.onPasswordChanged("secret1")

            viewModel.events.test {
                viewModel.submit()

                assertEquals(
                    UiEvent.NavigateTo(AppRoute.Home.route),
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(FakePendingAuthCredential, authProvider.linkedCredential)
            assertEquals(false, viewModel.uiState.value.isLinkingGoogle)
            assertEquals(null, viewModel.uiState.value.linkingGoogleEmail)
        }

    @Test
    fun `email mismatch after sign in clears pending Google credential and does not link`() =
        runTest {
            val authProvider =
                FakeAuthProvider(
                    googleSignInResult =
                        AuthResult.LinkRequired(
                            email = "google@example.com",
                            pendingCredential = FakePendingAuthCredential,
                        ),
                    signedInEmail = "password@example.com",
                )
            val viewModel = AuthViewModel(authProvider)

            viewModel.continueWithGoogle(FakeGoogleSignInHost)
            viewModel.onEmailChanged("password@example.com")
            viewModel.onPasswordChanged("secret1")
            viewModel.submit()
            runCurrent()

            assertEquals(null, authProvider.linkedCredential)
            assertEquals(false, viewModel.uiState.value.isLinkingGoogle)
            assertEquals(null, viewModel.uiState.value.linkingGoogleEmail)
            assertEquals(AuthMessage.GoogleLinkEmailMismatch, viewModel.uiState.value.message)

            authProvider.googleSignInResult = AuthResult.Success
            viewModel.continueWithGoogle(FakeGoogleSignInHost)

            assertEquals(2, authProvider.googleSignInCallCount)
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
        var googleSignInResult: AuthResult = AuthResult.Success,
        private val signedInEmail: String? = null,
        private val linkGoogleResult: AuthResult = AuthResult.Success,
    ) : AuthProvider {
        var signInCalled = false
        var createAccountCalled = false
        var googleSignInCalled = false
        var googleSignInCallCount = 0
        var linkedCredential: PendingAuthCredential? = null

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

        override suspend fun continueWithGoogle(host: GoogleSignInHost): AuthResult {
            googleSignInCalled = true
            googleSignInCallCount += 1
            return googleSignInResult
        }

        override fun currentUserId(): String? = null

        override fun currentUserEmail(): String? = signedInEmail

        override suspend fun linkWithGoogle(pendingCredential: PendingAuthCredential): AuthResult {
            linkedCredential = pendingCredential
            return linkGoogleResult
        }

        override fun isSignedIn(): Boolean = false

        override fun signOut() = Unit
    }

    private data object FakeGoogleSignInHost : GoogleSignInHost

    private data object FakePendingAuthCredential : PendingAuthCredential
}
