package com.bikedoc.android.api

import com.bikedoc.android.sessions.models.BikeSession
import com.bikedoc.android.sessions.models.BikeSessionCreate
import com.bikedoc.android.sessions.models.BikeSessionListResponse
import com.bikedoc.android.sessions.models.TurnAccepted
import com.bikedoc.android.sessions.models.TurnCreate
import javax.inject.Inject

interface SessionRepository {
    suspend fun getBikeSessions(
        bikeId: String,
        workflow: String,
    ): ApiResult<BikeSessionListResponse>

    suspend fun createBikeSession(body: BikeSessionCreate): ApiResult<BikeSession>

    suspend fun getBikeSession(sessionId: String): ApiResult<BikeSession>

    suspend fun createTurn(
        sessionId: String,
        body: TurnCreate,
    ): ApiResult<TurnAccepted>
}

class DefaultSessionRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
    ) : SessionRepository {
        override suspend fun getBikeSessions(
            bikeId: String,
            workflow: String,
        ): ApiResult<BikeSessionListResponse> = safeApiCall { apiService.getBikeSessions(bikeId, workflow).toDomain() }

        override suspend fun createBikeSession(body: BikeSessionCreate): ApiResult<BikeSession> =
            safeApiCall { apiService.createBikeSession(body.toDto()).toDomain() }

        override suspend fun getBikeSession(sessionId: String): ApiResult<BikeSession> =
            safeApiCall { apiService.getBikeSession(sessionId).toDomain() }

        override suspend fun createTurn(
            sessionId: String,
            body: TurnCreate,
        ): ApiResult<TurnAccepted> = safeApiCall { apiService.createTurn(sessionId, body.toDto()).toDomain() }
    }
