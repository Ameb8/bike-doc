package com.bikedoc.android.auth

import com.google.firebase.FirebaseException
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.FirebaseAuthException
import com.google.firebase.auth.FirebaseAuthInvalidCredentialsException
import com.google.firebase.auth.FirebaseAuthInvalidUserException
import com.google.firebase.auth.FirebaseAuthUserCollisionException
import com.google.firebase.auth.FirebaseAuthWeakPasswordException
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

        override suspend fun signIn(
            email: String,
            password: String,
        ): AuthResult =
            try {
                firebaseAuth().signInWithEmailAndPassword(email, password).await()
                AuthResult.Success
            } catch (_: AuthException) {
                AuthResult.Failure(AuthFailureReason.Unknown)
            } catch (exception: FirebaseException) {
                AuthResult.Failure(exception.toAuthFailureReasonForSignIn())
            }

        override suspend fun createAccount(
            email: String,
            password: String,
        ): AuthResult =
            try {
                firebaseAuth().createUserWithEmailAndPassword(email, password).await()
                AuthResult.Success
            } catch (_: AuthException) {
                AuthResult.Failure(AuthFailureReason.Unknown)
            } catch (exception: FirebaseException) {
                AuthResult.Failure(exception.toAuthFailureReasonForCreateAccount())
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

        private fun Exception.toAuthFailureReasonForSignIn(): AuthFailureReason =
            when (this) {
                is FirebaseAuthInvalidUserException -> AuthFailureReason.InvalidCredentials
                is FirebaseAuthInvalidCredentialsException ->
                    if (firebaseErrorCode() == "ERROR_INVALID_EMAIL") {
                        AuthFailureReason.InvalidEmail
                    } else {
                        AuthFailureReason.InvalidCredentials
                    }
                else -> AuthFailureReason.Unknown
            }

        private fun Exception.toAuthFailureReasonForCreateAccount(): AuthFailureReason =
            when (this) {
                is FirebaseAuthWeakPasswordException -> AuthFailureReason.WeakPassword
                is FirebaseAuthUserCollisionException -> AuthFailureReason.EmailAlreadyInUse
                is FirebaseAuthInvalidCredentialsException -> AuthFailureReason.InvalidEmail
                else -> AuthFailureReason.Unknown
            }

        private fun Exception.firebaseErrorCode(): String? = (this as? FirebaseAuthException)?.errorCode
    }
