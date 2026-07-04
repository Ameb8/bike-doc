package com.bikedoc.android.api

import com.bikedoc.android.api.models.AlternateHypothesisPayload
import com.bikedoc.android.api.models.DiagnosisPayload
import com.bikedoc.android.api.models.DiagnosticReportPayload
import com.bikedoc.android.api.models.SafetyFlag
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import timber.log.Timber
import javax.inject.Inject

interface ReportRepository {
    suspend fun getDiagnosticReport(
        sessionId: String,
        reportId: String,
    ): ApiResult<DiagnosticReport>
}

class DefaultReportRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
        private val json: Json,
    ) : ReportRepository {
        override suspend fun getDiagnosticReport(
            sessionId: String,
            reportId: String,
        ): ApiResult<DiagnosticReport> =
            when (val result = safeApiCall { apiService.getReport(sessionId, reportId) }) {
                is ApiResult.Success -> {
                    val envelope = result.data
                    if (envelope.type.isNotBlank() && envelope.type != DIAGNOSTIC_TYPE) {
                        ApiResult.Error(null, "This report is not a diagnostic report.")
                    } else {
                        try {
                            val payload =
                                json.decodeFromJsonElement(
                                    DiagnosticReportPayload.serializer(),
                                    envelope.payload,
                                )
                            ApiResult.Success(payload.toDiagnosticReport(envelope.id, envelope.createdAt))
                        } catch (exception: SerializationException) {
                            Timber.e(exception, "Diagnostic report payload decoding failed")
                            ApiResult.Error(null, "Unexpected diagnostic report format.")
                        } catch (exception: IllegalArgumentException) {
                            Timber.e(exception, "Diagnostic report payload validation failed")
                            ApiResult.Error(null, exception.message ?: "Unexpected diagnostic report format.")
                        }
                    }
                }

                is ApiResult.Error -> result
                ApiResult.Loading -> ApiResult.Loading
            }

        private fun DiagnosticReportPayload.toDiagnosticReport(
            reportId: String,
            createdAt: String,
        ): DiagnosticReport {
            require(schemaVersion == DIAGNOSTIC_SCHEMA_VERSION) {
                "Unexpected diagnostic report version."
            }
            return DiagnosticReport(
                id = reportId,
                createdAt = createdAt,
                primaryDiagnosis = primaryDiagnosis.toDiagnosis(),
                alternateHypotheses = alternateHypotheses.map { it.toAlternateHypothesis() },
                evidenceSummary = evidenceSummary,
                userSkillLevel = userSkillLevel,
                safetyFlags = safetyFlags,
                keyArtifactIds = keyArtifactIds,
            )
        }

        private fun DiagnosisPayload.toDiagnosis(): Diagnosis =
            Diagnosis(
                component = component,
                issue = issue,
                confidence = confidence,
                diySuitability = diySuitability ?: UNKNOWN_VALUE,
            )

        private fun AlternateHypothesisPayload.toAlternateHypothesis(): AlternateHypothesis =
            AlternateHypothesis(
                component = component,
                issue = issue,
                confidence = confidence,
                ruledOutBy = ruledOutBy,
            )

        private companion object {
            const val DIAGNOSTIC_TYPE = "diagnostic"
            const val DIAGNOSTIC_SCHEMA_VERSION = "diagnostic_report.v1"
            const val UNKNOWN_VALUE = "unknown"
        }
    }

data class DiagnosticReport(
    val id: String,
    val createdAt: String,
    val primaryDiagnosis: Diagnosis,
    val alternateHypotheses: List<AlternateHypothesis>,
    val evidenceSummary: String,
    val userSkillLevel: String,
    val safetyFlags: List<SafetyFlag>,
    val keyArtifactIds: List<String>,
)

data class Diagnosis(
    val component: String,
    val issue: String,
    val confidence: String,
    val diySuitability: String,
)

data class AlternateHypothesis(
    val component: String,
    val issue: String,
    val confidence: String,
    val ruledOutBy: String?,
)
