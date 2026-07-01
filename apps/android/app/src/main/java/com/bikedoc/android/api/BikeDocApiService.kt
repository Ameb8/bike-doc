package com.bikedoc.android.api

import com.bikedoc.android.api.models.Bike
import com.bikedoc.android.api.models.BikeCreate
import com.bikedoc.android.api.models.BikeListResponse
import com.bikedoc.android.api.models.BikePatch
import com.bikedoc.android.api.models.PhaseReportEnvelope
import com.bikedoc.android.api.models.PhaseReportList
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.api.models.RepairSessionCreate
import com.bikedoc.android.api.models.RepairSessionListResponse
import com.bikedoc.android.api.models.TurnAccepted
import com.bikedoc.android.api.models.TurnCreate
import com.bikedoc.android.api.models.UserProfile
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

interface BikeDocApiService {
    @GET("v1/me")
    suspend fun getMe(): UserProfile

    @GET("v1/bikes")
    suspend fun getBikes(
        @Query("limit") limit: Int = 50,
        @Query("cursor") cursor: String? = null,
    ): BikeListResponse

    @POST("v1/bikes")
    suspend fun createBike(
        @Body bike: BikeCreate,
    ): Bike

    @GET("v1/bikes/{bikeId}")
    suspend fun getBike(
        @Path("bikeId") bikeId: String,
    ): Bike

    @PATCH("v1/bikes/{bikeId}")
    suspend fun updateBike(
        @Path("bikeId") bikeId: String,
        @Body bike: BikePatch,
    ): Bike

    @DELETE("v1/bikes/{bikeId}")
    suspend fun deleteBike(
        @Path("bikeId") bikeId: String,
    )

    @GET("v1/repair-sessions")
    suspend fun getRepairSessions(
        @Query("bike_id") bikeId: String,
        @Query("limit") limit: Int? = null,
        @Query("cursor") cursor: String? = null,
    ): RepairSessionListResponse

    @POST("v1/repair-sessions")
    suspend fun createRepairSession(
        @Body body: RepairSessionCreate,
    ): RepairSession

    @GET("v1/repair-sessions/{sessionId}")
    suspend fun getRepairSession(
        @Path("sessionId") sessionId: String,
    ): RepairSession

    @POST("v1/repair-sessions/{sessionId}/turns")
    suspend fun createTurn(
        @Path("sessionId") sessionId: String,
        @Body body: TurnCreate,
    ): TurnAccepted

    @GET("v1/repair-sessions/{sessionId}/reports")
    suspend fun getReports(
        @Path("sessionId") sessionId: String,
        @Query("limit") limit: Int = 50,
        @Query("cursor") cursor: String? = null,
    ): PhaseReportList

    @GET("v1/repair-sessions/{sessionId}/reports/{reportId}")
    suspend fun getReport(
        @Path("sessionId") sessionId: String,
        @Path("reportId") reportId: String,
    ): PhaseReportEnvelope
}
