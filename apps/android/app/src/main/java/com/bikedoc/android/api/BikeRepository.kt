package com.bikedoc.android.api

import com.bikedoc.android.bikes.BikeProfile
import com.bikedoc.android.bikes.BikeProfileEdit
import com.bikedoc.android.bikes.toCreateDto
import com.bikedoc.android.bikes.toDomain
import com.bikedoc.android.bikes.toPatchDto
import javax.inject.Inject

interface BikeRepository {
    suspend fun getBikes(): ApiResult<List<BikeProfile>>

    suspend fun createBike(bike: BikeProfileEdit): ApiResult<BikeProfile>

    suspend fun getBike(bikeId: String): ApiResult<BikeProfile>

    suspend fun updateBike(
        bikeId: String,
        bike: BikeProfileEdit,
    ): ApiResult<BikeProfile>

    suspend fun deleteBike(bikeId: String): ApiResult<Unit>
}

class DefaultBikeRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
    ) : BikeRepository {
        override suspend fun getBikes(): ApiResult<List<BikeProfile>> {
            return safeApiCall { apiService.getBikes().items.map { it.toDomain() } }
        }

        override suspend fun createBike(bike: BikeProfileEdit): ApiResult<BikeProfile> =
            safeApiCall { apiService.createBike(bike.toCreateDto()).toDomain() }

        override suspend fun getBike(bikeId: String): ApiResult<BikeProfile> {
            return safeApiCall { apiService.getBike(bikeId).toDomain() }
        }

        override suspend fun updateBike(
            bikeId: String,
            bike: BikeProfileEdit,
        ): ApiResult<BikeProfile> {
            return safeApiCall { apiService.updateBike(bikeId, bike.toPatchDto()).toDomain() }
        }

        override suspend fun deleteBike(bikeId: String): ApiResult<Unit> {
            return safeApiCall { apiService.deleteBike(bikeId) }
        }
    }
