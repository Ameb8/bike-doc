package com.bikedoc.android.api

import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.api.models.RepairSessionCreate
import com.bikedoc.android.api.models.RepairSessionListResponse
import com.bikedoc.android.api.models.TurnAccepted
import com.bikedoc.android.api.models.TurnCreate
import javax.inject.Inject

interface SessionRepository {
    suspend fun getRepairSessions(bikeId: String): ApiResult<RepairSessionListResponse>

    suspend fun createRepairSession(body: RepairSessionCreate): ApiResult<RepairSession>

    suspend fun getRepairSession(sessionId: String): ApiResult<RepairSession>

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
        override suspend fun getRepairSessions(bikeId: String): ApiResult<RepairSessionListResponse> =
            safeApiCall { apiService.getRepairSessions(bikeId) }

        override suspend fun createRepairSession(body: RepairSessionCreate): ApiResult<RepairSession> =
            safeApiCall { apiService.createRepairSession(body) }

        override suspend fun getRepairSession(sessionId: String): ApiResult<RepairSession> =
            safeApiCall { apiService.getRepairSession(sessionId) }

        override suspend fun createTurn(
            sessionId: String,
            body: TurnCreate,
        ): ApiResult<TurnAccepted> = safeApiCall { apiService.createTurn(sessionId, body) }
    }
