package com.bikedoc.android.api

import com.bikedoc.android.auth.AuthException
import com.bikedoc.android.auth.AuthProvider
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.Response
import timber.log.Timber
import javax.inject.Inject

class AuthInterceptor
    @Inject
    constructor(
        private val authProvider: AuthProvider,
    ) : Interceptor {
        override fun intercept(chain: Interceptor.Chain): Response {
            val firstRequest = chain.request().withBearerToken(forceRefresh = false)
            val firstResponse = chain.proceed(firstRequest)
            if (firstResponse.code != 401) {
                return firstResponse
            }

            firstResponse.close()
            val retryRequest = chain.request().withBearerToken(forceRefresh = true)
            val retryResponse = chain.proceed(retryRequest)
            if (retryResponse.code == 401) {
                authProvider.signOut()
            }
            return retryResponse
        }

        private fun okhttp3.Request.withBearerToken(forceRefresh: Boolean): okhttp3.Request =
            try {
                val token = runBlocking { authProvider.getToken(forceRefresh) }
                newBuilder()
                    .header("Authorization", "Bearer $token")
                    .build()
            } catch (exception: AuthException) {
                Timber.d(exception, "Skipping auth header because no Firebase user is signed in.")
                this
            }
    }
