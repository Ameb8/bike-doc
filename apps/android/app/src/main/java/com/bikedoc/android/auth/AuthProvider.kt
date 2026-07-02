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
    Unknown,
}

sealed interface AuthResult {
    data object Success : AuthResult

    data class Failure(
        val reason: AuthFailureReason,
    ) : AuthResult
}

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

    fun currentUserId(): String?

    fun isSignedIn(): Boolean

    fun signOut()
}
