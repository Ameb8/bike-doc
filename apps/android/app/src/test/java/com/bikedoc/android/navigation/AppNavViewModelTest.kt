package com.bikedoc.android.navigation

import com.bikedoc.android.auth.AuthFailureReason
import com.bikedoc.android.auth.AuthProvider
import com.bikedoc.android.auth.AuthResult
import com.bikedoc.android.auth.GoogleSignInHost
import com.bikedoc.android.auth.PendingAuthCredential
import org.junit.Assert.assertEquals
import org.junit.Test

class AppNavViewModelTest {
    @Test
    fun `starts on auth when there is no signed in user`() {
        val viewModel = AppNavViewModel(FakeAuthProvider(signedIn = false))

        assertEquals(AppRoute.Auth.route, viewModel.startRoute.value)
    }

    @Test
    fun `starts on home when there is a signed in user`() {
        val viewModel = AppNavViewModel(FakeAuthProvider(signedIn = true))

        assertEquals(AppRoute.Home.route, viewModel.startRoute.value)
    }

    private class FakeAuthProvider(
        private val signedIn: Boolean,
    ) : AuthProvider {
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

        override fun signOut() = Unit
    }
}
