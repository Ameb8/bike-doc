package com.bikedoc.android.auth

import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialException
import androidx.credentials.exceptions.NoCredentialException
import com.bikedoc.android.R
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import com.google.android.libraries.identity.googleid.GoogleIdTokenParsingException
import com.google.firebase.FirebaseException
import com.google.firebase.auth.AuthCredential
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.FirebaseAuthException
import com.google.firebase.auth.FirebaseAuthInvalidCredentialsException
import com.google.firebase.auth.FirebaseAuthInvalidUserException
import com.google.firebase.auth.FirebaseAuthUserCollisionException
import com.google.firebase.auth.FirebaseAuthWeakPasswordException
import com.google.firebase.auth.GoogleAuthProvider
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

        override suspend fun continueWithGoogle(host: GoogleSignInHost): AuthResult =
            when (val result = requestGoogleIdTokenForHost(host)) {
                GoogleIdTokenRequestResult.Cancelled -> AuthResult.Cancelled
                is GoogleIdTokenRequestResult.Failure -> AuthResult.Failure(result.reason)
                is GoogleIdTokenRequestResult.Success -> signInToFirebaseWithGoogle(result.idToken)
            }

        override suspend fun linkWithGoogle(pendingCredential: PendingAuthCredential): AuthResult {
            val credential =
                (pendingCredential as? FirebasePendingAuthCredential)?.credential
            val user =
                firebaseAuthOrNull()?.currentUser
            return if (credential == null || user == null) {
                AuthResult.Failure(AuthFailureReason.FirebaseSignInFailed)
            } else {
                try {
                    user.linkWithCredential(credential).await()
                    AuthResult.Success
                } catch (_: FirebaseException) {
                    AuthResult.Failure(AuthFailureReason.FirebaseSignInFailed)
                } catch (_: IllegalStateException) {
                    AuthResult.Failure(AuthFailureReason.FirebaseSignInFailed)
                }
            }
        }

        override fun currentUserId(): String? = firebaseAuthOrNull()?.currentUser?.uid

        override fun currentUserEmail(): String? = firebaseAuthOrNull()?.currentUser?.email

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

        private suspend fun requestGoogleIdTokenForHost(host: GoogleSignInHost): GoogleIdTokenRequestResult =
            (host as? AndroidGoogleSignInHost)
                ?.let { requestGoogleIdToken(it) }
                ?: GoogleIdTokenRequestResult.Failure(
                    AuthFailureReason.GoogleProviderUnavailable,
                )

        private suspend fun requestGoogleIdToken(host: AndroidGoogleSignInHost): GoogleIdTokenRequestResult {
            val context = host.context
            val webClientId = context.getString(R.string.default_web_client_id)
            if (webClientId.isBlank()) {
                return GoogleIdTokenRequestResult.Failure(
                    AuthFailureReason.GoogleProviderUnavailable,
                )
            }
            val googleIdOption =
                GetGoogleIdOption.Builder()
                    .setFilterByAuthorizedAccounts(false)
                    .setServerClientId(webClientId)
                    .build()
            val request =
                GetCredentialRequest.Builder()
                    .addCredentialOption(googleIdOption)
                    .build()

            return try {
                val response =
                    CredentialManager
                        .create(context)
                        .getCredential(
                            context = context,
                            request = request,
                        )
                val credential = response.credential
                if (
                    credential is CustomCredential &&
                    credential.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL
                ) {
                    GoogleIdTokenCredential
                        .createFrom(credential.data)
                        .idToken
                        .takeUnless(String::isBlank)
                        ?.let(GoogleIdTokenRequestResult::Success)
                        ?: GoogleIdTokenRequestResult.Failure(
                            AuthFailureReason.MissingGoogleIdToken,
                        )
                } else {
                    GoogleIdTokenRequestResult.Failure(AuthFailureReason.MissingGoogleIdToken)
                }
            } catch (_: GetCredentialCancellationException) {
                GoogleIdTokenRequestResult.Cancelled
            } catch (_: NoCredentialException) {
                GoogleIdTokenRequestResult.Failure(AuthFailureReason.NoGoogleCredential)
            } catch (_: GoogleIdTokenParsingException) {
                GoogleIdTokenRequestResult.Failure(AuthFailureReason.MissingGoogleIdToken)
            } catch (_: GetCredentialException) {
                GoogleIdTokenRequestResult.Failure(AuthFailureReason.GoogleProviderUnavailable)
            }
        }

        private suspend fun signInToFirebaseWithGoogle(idToken: String): AuthResult {
            val firebaseCredential = GoogleAuthProvider.getCredential(idToken, null)
            return try {
                FirebaseAuth.getInstance().signInWithCredential(firebaseCredential).await()
                AuthResult.Success
            } catch (exception: FirebaseAuthUserCollisionException) {
                AuthResult.LinkRequired(
                    email = exception.email,
                    pendingCredential = FirebasePendingAuthCredential(firebaseCredential),
                )
            } catch (_: FirebaseException) {
                AuthResult.Failure(AuthFailureReason.FirebaseSignInFailed)
            } catch (_: IllegalStateException) {
                AuthResult.Failure(AuthFailureReason.FirebaseSignInFailed)
            }
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

        private data class FirebasePendingAuthCredential(
            val credential: AuthCredential,
        ) : PendingAuthCredential

        private sealed interface GoogleIdTokenRequestResult {
            data class Success(
                val idToken: String,
            ) : GoogleIdTokenRequestResult

            data object Cancelled : GoogleIdTokenRequestResult

            data class Failure(
                val reason: AuthFailureReason,
            ) : GoogleIdTokenRequestResult
        }
    }
