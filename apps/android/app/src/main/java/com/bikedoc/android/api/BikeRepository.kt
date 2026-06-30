package com.bikedoc.android.api

import com.bikedoc.android.api.models.Bike
import com.bikedoc.android.api.models.BikeCreate
import com.bikedoc.android.api.models.BikeListResponse
import com.bikedoc.android.api.models.BikePatch
import javax.inject.Inject

interface BikeRepository {
    suspend fun getBikes(): ApiResult<BikeListResponse>

    suspend fun createBike(bike: BikeCreate): ApiResult<Bike>

    suspend fun getBike(bikeId: String): ApiResult<Bike>

    suspend fun updateBike(
        bikeId: String,
        bike: BikePatch,
    ): ApiResult<Bike>

    suspend fun deleteBike(bikeId: String): ApiResult<Unit>
}

class DefaultBikeRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
    ) : BikeRepository {
        override suspend fun getBikes(): ApiResult<BikeListResponse> = safeApiCall { apiService.getBikes() }

        override suspend fun createBike(bike: BikeCreate): ApiResult<Bike> = safeApiCall { apiService.createBike(bike) }

        override suspend fun getBike(bikeId: String): ApiResult<Bike> = safeApiCall { apiService.getBike(bikeId) }

        override suspend fun updateBike(
            bikeId: String,
            bike: BikePatch,
        ): ApiResult<Bike> = safeApiCall { apiService.updateBike(bikeId, bike) }

        override suspend fun deleteBike(bikeId: String): ApiResult<Unit> = safeApiCall { apiService.deleteBike(bikeId) }
    }
