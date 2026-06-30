package com.bikedoc.android.auth

interface AuthProvider {
    suspend fun getToken(forceRefresh: Boolean = false): String

    fun currentUserId(): String?

    fun isSignedIn(): Boolean

    fun signOut()
}
