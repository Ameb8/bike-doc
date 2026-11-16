package com.bikedoc.android.api

import com.bikedoc.android.api.models.ArtifactUploadResponse
import com.bikedoc.android.api.models.BikeListResponseDto
import com.bikedoc.android.api.models.BikeProfileDto
import com.bikedoc.android.api.models.BikeSessionCreateDto
import com.bikedoc.android.api.models.BikeSessionDto
import com.bikedoc.android.api.models.BikeSessionListResponseDto
import com.bikedoc.android.api.models.PhaseReportEnvelope
import com.bikedoc.android.api.models.PhaseReportList
import com.bikedoc.android.api.models.TurnAcceptedDto
import com.bikedoc.android.api.models.TurnCreateDto
import com.bikedoc.android.api.models.UserProfile
import kotlinx.serialization.json.JsonObject
import okhttp3.MultipartBody
import okhttp3.RequestBody
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query

interface BikeDocApiService {
    @GET("v1/me")
    suspend fun getMe(): UserProfile

    @GET("v1/bikes")
    suspend fun getBikes(
        @Query("limit") limit: Int = 50,
        @Query("cursor") cursor: String? = null,
    ): BikeListResponseDto

    @POST("v1/bikes")
    suspend fun createBike(
        @Body bike: JsonObject,
    ): BikeProfileDto

    @GET("v1/bikes/{bikeId}")
    suspend fun getBike(
        @Path("bikeId") bikeId: String,
    ): BikeProfileDto

    @PATCH("v1/bikes/{bikeId}")
    suspend fun updateBike(
        @Path("bikeId") bikeId: String,
        @Body bike: JsonObject,
    ): BikeProfileDto

    @DELETE("v1/bikes/{bikeId}")
    suspend fun deleteBike(
        @Path("bikeId") bikeId: String,
    )

    @GET("v1/sessions")
    suspend fun getBikeSessions(
        @Query("bike_id") bikeId: String,
        @Query("workflow") workflow: String,
        @Query("limit") limit: Int? = null,
        @Query("cursor") cursor: String? = null,
    ): BikeSessionListResponseDto

    @POST("v1/sessions")
    suspend fun createBikeSession(
        @Body body: BikeSessionCreateDto,
    ): BikeSessionDto

    @GET("v1/sessions/{sessionId}")
    suspend fun getBikeSession(
        @Path("sessionId") sessionId: String,
    ): BikeSessionDto

    @POST("v1/sessions/{sessionId}/turns")
    suspend fun createTurn(
        @Path("sessionId") sessionId: String,
        @Body body: TurnCreateDto,
    ): TurnAcceptedDto

    @Multipart
    @POST("v1/artifacts")
    suspend fun uploadArtifact(
        @Part file: MultipartBody.Part,
        @Part("purpose") purpose: RequestBody,
        @Part("session_id") sessionId: RequestBody,
        @Part("client_artifact_id") clientArtifactId: RequestBody? = null,
    ): ArtifactUploadResponse

    @GET("v1/sessions/{sessionId}/reports")
    suspend fun getReports(
        @Path("sessionId") sessionId: String,
        @Query("limit") limit: Int = 50,
        @Query("cursor") cursor: String? = null,
    ): PhaseReportList

    @GET("v1/sessions/{sessionId}/reports/{reportId}")
    suspend fun getReport(
        @Path("sessionId") sessionId: String,
        @Path("reportId") reportId: String,
    ): PhaseReportEnvelope
}
