package com.bikedoc.android.api

import com.bikedoc.android.auth.AuthFailureReason
import com.bikedoc.android.auth.AuthProvider
import com.bikedoc.android.auth.AuthResult
import kotlinx.coroutines.ExperimentalCoroutinesApi
import okhttp3.Call
import okhttp3.Connection
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Timeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AuthInterceptorTest {
    @Test
    fun `retries once with forced refresh and signs out after second unauthorized`() {
        val authProvider = FakeAuthProvider()
        val interceptor = AuthInterceptor(authProvider)
        val chain = FakeChain(responseCodes = listOf(401, 401))

        val response = interceptor.intercept(chain)

        assertEquals(401, response.code)
        assertEquals(listOf(false, true), authProvider.tokenRefreshes)
        assertEquals(
            listOf("Bearer token-0", "Bearer token-1"),
            chain.authorizationHeaders,
        )
        assertTrue(authProvider.signOutCalled)
    }

    private class FakeAuthProvider : AuthProvider {
        val tokenRefreshes = mutableListOf<Boolean>()
        var signOutCalled = false

        override suspend fun getToken(forceRefresh: Boolean): String {
            tokenRefreshes += forceRefresh
            return "token-${tokenRefreshes.lastIndex}"
        }

        override suspend fun signIn(
            email: String,
            password: String,
        ): AuthResult = AuthResult.Failure(AuthFailureReason.Unknown)

        override suspend fun createAccount(
            email: String,
            password: String,
        ): AuthResult = AuthResult.Failure(AuthFailureReason.Unknown)

        override fun currentUserId(): String? = "user-1"

        override fun isSignedIn(): Boolean = true

        override fun signOut() {
            signOutCalled = true
        }
    }

    private class FakeChain(
        private val responseCodes: List<Int>,
    ) : Interceptor.Chain {
        private var index = 0
        val authorizationHeaders = mutableListOf<String?>()

        override fun request(): Request = Request.Builder().url("https://example.com/v1/me").build()

        override fun proceed(request: Request): Response {
            authorizationHeaders += request.header("Authorization")
            val code = responseCodes[index++]
            return Response.Builder()
                .request(request)
                .protocol(Protocol.HTTP_1_1)
                .code(code)
                .message("test")
                .body("{}".toResponseBody("application/json".toMediaType()))
                .build()
        }

        override fun call(): Call =
            object : Call {
                override fun request(): Request = this@FakeChain.request()

                override fun execute(): Response = error("Not used")

                override fun enqueue(responseCallback: okhttp3.Callback) = Unit

                override fun cancel() = Unit

                override fun isExecuted(): Boolean = false

                override fun isCanceled(): Boolean = false

                override fun timeout(): Timeout = Timeout.NONE

                override fun clone(): Call = this
            }

        override fun connection(): Connection? = null

        override fun connectTimeoutMillis(): Int = 0

        override fun withConnectTimeout(
            timeout: Int,
            unit: java.util.concurrent.TimeUnit,
        ): Interceptor.Chain = this

        override fun readTimeoutMillis(): Int = 0

        override fun withReadTimeout(
            timeout: Int,
            unit: java.util.concurrent.TimeUnit,
        ): Interceptor.Chain = this

        override fun writeTimeoutMillis(): Int = 0

        override fun withWriteTimeout(
            timeout: Int,
            unit: java.util.concurrent.TimeUnit,
        ): Interceptor.Chain = this
    }
}
