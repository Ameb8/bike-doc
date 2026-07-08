package com.bikedoc.android.auth

enum class AuthMode {
    SignIn,
    CreateAccount,
}

enum class AuthFailureReason {
    InvalidCredentials,
    InvalidEmail,
    WeakPassword,
    EmailAlreadyInUse,
    NoGoogleCredential,
    GoogleProviderUnavailable,
    MissingGoogleIdToken,
    FirebaseSignInFailed,
    Unknown,
}

sealed interface AuthResult {
    data object Success : AuthResult

    data object Cancelled : AuthResult

    data class Failure(
        val reason: AuthFailureReason,
    ) : AuthResult
}

interface GoogleSignInHost

interface AuthProvider {
    suspend fun getToken(forceRefresh: Boolean = false): String

    suspend fun signIn(
        email: String,
        password: String,
    ): AuthResult

    suspend fun createAccount(
        email: String,
        password: String,
    ): AuthResult

    suspend fun continueWithGoogle(host: GoogleSignInHost): AuthResult

    fun currentUserId(): String?

    fun isSignedIn(): Boolean

    fun signOut()
}
