package com.bikedoc.android.home

import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.BikeDocApiService
import com.bikedoc.android.api.BikeRepository
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.api.models.RepairSessionCreate
import com.bikedoc.android.bikes.BikeProfileEdit
import javax.inject.Inject

data class BikeDocUser(
    val id: String,
    val displayName: String?,
)

data class HomeBike(
    val id: String,
    val name: String,
)

interface HomeRepository {
    suspend fun getCurrentUser(): ApiResult<BikeDocUser>

    suspend fun getBikes(): ApiResult<List<HomeBike>>

    suspend fun startRepair(bikeId: String?): ApiResult<RepairSession>
}

class DefaultHomeRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
        private val bikeRepository: BikeRepository,
        private val sessionRepository: SessionRepository,
    ) : HomeRepository {
        override suspend fun getCurrentUser(): ApiResult<BikeDocUser> =
            com.bikedoc.android.api.safeApiCall {
                val profile = apiService.getMe()
                BikeDocUser(
                    id = profile.id,
                    displayName = profile.displayName,
                )
            }

        override suspend fun getBikes(): ApiResult<List<HomeBike>> =
            when (val result = bikeRepository.getBikes()) {
                is ApiResult.Success ->
                    ApiResult.Success(
                        result.data.map { bike -> HomeBike(id = bike.id, name = bike.displayName) },
                    )
                is ApiResult.Error -> result
                ApiResult.Loading -> ApiResult.Loading
            }

        @Suppress("ReturnCount")
        override suspend fun startRepair(bikeId: String?): ApiResult<RepairSession> {
            val repairBikeId =
                bikeId
                    ?: when (val result = bikeRepository.createBike(BikeProfileEdit(displayName = NEW_BIKE_NAME))) {
                        is ApiResult.Success -> result.data.id
                        is ApiResult.Error -> return result
                        ApiResult.Loading -> return ApiResult.Loading
                    }
            return sessionRepository.createRepairSession(RepairSessionCreate(bikeId = repairBikeId))
        }

        private companion object {
            const val NEW_BIKE_NAME = "New bike"
        }
    }
