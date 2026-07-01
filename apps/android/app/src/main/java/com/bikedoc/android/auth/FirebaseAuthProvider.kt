package com.bikedoc.android.auth

import com.google.firebase.auth.FirebaseAuth
import kotlinx.coroutines.tasks.await
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class FirebaseAuthProvider
    @Inject
    constructor() : AuthProvider {
        override suspend fun getToken(forceRefresh: Boolean): String {
            val user = firebaseAuth().currentUser ?: throw AuthException("No signed-in user")
            return user.getIdToken(forceRefresh).await().token
                ?: throw AuthException("Token retrieval returned null")
        }

        override fun currentUserId(): String? = firebaseAuthOrNull()?.currentUser?.uid

        override fun isSignedIn(): Boolean = firebaseAuthOrNull()?.currentUser != null

        override fun signOut() {
            firebaseAuthOrNull()?.signOut()
        }

        private fun firebaseAuth(): FirebaseAuth =
            firebaseAuthOrNull()
                ?: throw AuthException("Firebase Auth is not configured")

        private fun firebaseAuthOrNull(): FirebaseAuth? =
            try {
                FirebaseAuth.getInstance()
            } catch (_: IllegalStateException) {
                null
            }
    }
