package com.bikedoc.android.home

import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.BikeDocApiService
import javax.inject.Inject

data class BikeDocUser(
    val id: String,
    val displayName: String?,
)

interface HomeRepository {
    suspend fun getCurrentUser(): ApiResult<BikeDocUser>
}

class DefaultHomeRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
    ) : HomeRepository {
        override suspend fun getCurrentUser(): ApiResult<BikeDocUser> =
            com.bikedoc.android.api.safeApiCall {
                val profile = apiService.getMe()
                BikeDocUser(
                    id = profile.id,
                    displayName = profile.displayName,
                )
            }
    }
