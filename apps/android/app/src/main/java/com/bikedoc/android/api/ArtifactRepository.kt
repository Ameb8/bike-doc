package com.bikedoc.android.api

import com.bikedoc.android.api.models.ArtifactRef
import com.bikedoc.android.api.models.PreparedDiagnosticPhoto
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import javax.inject.Inject

interface ArtifactRepository {
    suspend fun uploadDiagnosticPhoto(
        sessionId: String,
        photo: PreparedDiagnosticPhoto,
    ): ApiResult<ArtifactRef>
}

class DefaultArtifactRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
    ) : ArtifactRepository {
        override suspend fun uploadDiagnosticPhoto(
            sessionId: String,
            photo: PreparedDiagnosticPhoto,
        ): ApiResult<ArtifactRef> =
            when (
                val result =
                    safeApiCall {
                        apiService.uploadArtifact(
                            file =
                                MultipartBody.Part.createFormData(
                                    name = "file",
                                    filename = photo.fileName,
                                    body = photo.bytes.toRequestBody(photo.mimeType.toMediaType()),
                                ),
                            purpose = "diagnostic_photo".toRequestBody(MULTIPART_TEXT.toMediaType()),
                            repairSessionId = sessionId.toRequestBody(MULTIPART_TEXT.toMediaType()),
                            clientArtifactId = photo.clientArtifactId?.toRequestBody(MULTIPART_TEXT.toMediaType()),
                        )
                    }
            ) {
                is ApiResult.Success -> ApiResult.Success(result.data.artifact)
                is ApiResult.Error -> result
                ApiResult.Loading -> ApiResult.Loading
            }

        private companion object {
            const val MULTIPART_TEXT = "text/plain"
        }
    }
