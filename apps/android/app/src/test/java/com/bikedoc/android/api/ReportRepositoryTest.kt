package com.bikedoc.android.api

import com.bikedoc.android.api.models.ArtifactUploadResponse
import com.bikedoc.android.api.models.BikeListResponseDto
import com.bikedoc.android.api.models.BikeProfileDto
import com.bikedoc.android.api.models.DiagnosticConfidencePayload
import com.bikedoc.android.api.models.DiagnosticOutcomePayload
import com.bikedoc.android.api.models.DiagnosticRelevancePayload
import com.bikedoc.android.api.models.DiySuitabilityPayload
import com.bikedoc.android.api.models.EvidenceSourcePayload
import com.bikedoc.android.api.models.PhaseReportEnvelope
import com.bikedoc.android.api.models.PhaseReportList
import com.bikedoc.android.api.models.RepairSession
import com.bikedoc.android.api.models.RepairSessionCreate
import com.bikedoc.android.api.models.RepairSessionListResponse
import com.bikedoc.android.api.models.TurnAccepted
import com.bikedoc.android.api.models.TurnCreate
import com.bikedoc.android.api.models.UserProfile
import com.bikedoc.android.api.models.UserSkillLevelPayload
import com.bikedoc.android.di.CoreModule
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import okhttp3.MultipartBody
import okhttp3.RequestBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReportRepositoryTest {
    @Test
    fun `maps every supported V2 nested type at the repository boundary`() = runTest {
        val report =
            repositoryFor(envelope(v2SupportedPayload()))
                .getDiagnosticReport("session-1", "report-1")

        val mapped = assertSuccess<DiagnosticReportV2>(report)
        assertEquals(DiagnosticOutcome.DIAGNOSIS_SUPPORTED, mapped.diagnosticOutcome)
        assertEquals(listOf("Chain skips under load."), mapped.reportedSymptoms)
        assertEquals(listOf("finding-1"), mapped.primaryDiagnosis?.supportingFindingIds)
        assertEquals(EvidenceSource.IMAGE, mapped.observedFindings.single().evidenceSource)
        assertEquals(
            DiagnosticRelevance.SUPPORTS_PRIMARY_DIAGNOSIS,
            mapped.observedFindings.single().relationshipToSymptoms,
        )
        assertEquals("artifact-1", mapped.observedFindings.single().artifactIds.single())
        assertEquals("Indexing is slightly out.", mapped.contributingFactors.single().issue)
        assertEquals("Freehub engagement", mapped.alternateHypotheses.single().issue)
        assertEquals("Hanger alignment was not measured.", mapped.unresolvedUncertainties.single())
        assertEquals("phase-session-1", mapped.diagnosticSessionId)
    }

    @Test
    fun `maps a limited V2 report with null primary diagnosis and empty collections`() = runTest {
        val report =
            repositoryFor(envelope(v2LimitedPayload()))
                .getDiagnosticReport("session-1", "report-1")

        val mapped = assertSuccess<DiagnosticReportV2>(report)
        assertEquals(
            DiagnosticOutcome.IN_PERSON_ASSESSMENT_REQUIRED,
            mapped.diagnosticOutcome,
        )
        assertNull(mapped.primaryDiagnosis)
        assertTrue(mapped.contributingFactors.isEmpty())
        assertTrue(mapped.alternateHypotheses.isEmpty())
        assertTrue(mapped.unresolvedUncertainties.isEmpty())
        assertEquals(
            EvidenceSource.USER_REPORT,
            mapped.observedFindings.single().evidenceSource,
        )
    }

    @Test
    fun `preserves V1 diagnosis repair estimate and ruled out evidence`() = runTest {
        val report =
            repositoryFor(envelope(v1Payload()))
                .getDiagnosticReport("session-1", "report-1")

        val mapped = assertSuccess<DiagnosticReport>(report)
        assertEquals("Bent hanger", mapped.primaryDiagnosis.issue)
        assertEquals("Chain wear measurement", mapped.alternateHypotheses.single().ruledOutBy)
        assertEquals(30, mapped.repairEstimate.repairTime.lowMinutes)
        assertEquals(120, mapped.repairEstimate.shopRepairCost.highUsd)
    }

    @Test
    fun `rejects unknown and envelope payload mismatched report versions`() = runTest {
        val unknown = repositoryFor(envelope(v2SupportedPayload().replace("v2", "v3")))
            .getDiagnosticReport("session-1", "report-1")
        val mismatched =
            repositoryFor(envelope(v2SupportedPayload(), schemaVersion = "diagnostic_report.v1"))
                .getDiagnosticReport("session-1", "report-1")

        assertVersionError(unknown)
        assertVersionError(mismatched)
    }

    @Test
    fun `rejects malformed V2 payloads without guessing a version`() = runTest {
        val malformed =
            repositoryFor(
                envelope(
                    """{"schema_version":"diagnostic_report.v2","diagnostic_outcome":"unknown"}""",
                ),
            ).getDiagnosticReport("session-1", "report-1")

        assertTrue(malformed is ApiResult.Error)
        assertEquals("Unexpected diagnostic report format.", (malformed as ApiResult.Error).message)
    }

    @Test
    fun `V2 enum DTOs use the OpenAPI wire values`() {
        val json = CoreModule.provideJson()

        assertEquals(
            "[\"diagnosis_supported\",\"user_declined_more_input\",\"requested_input_unavailable\",\"in_person_assessment_required\"]",
            json.encodeToString(DiagnosticOutcomePayload.values().toList()),
        )
        assertEquals(
            "[\"image\",\"user_report\",\"measurement\",\"functional_check\",\"repair_history\",\"other\"]",
            json.encodeToString(EvidenceSourcePayload.values().toList()),
        )
        assertEquals(
            "[\"unknown\",\"possible_contributor\",\"supports_primary_diagnosis\",\"supported_contributor\",\"incidental\"]",
            json.encodeToString(DiagnosticRelevancePayload.values().toList()),
        )
        assertEquals(
            "[\"low\",\"medium\",\"high\"]",
            json.encodeToString(DiagnosticConfidencePayload.values().toList()),
        )
        assertEquals(
            "[\"unknown\",\"reasonable\",\"caution\",\"shop_recommended\",\"blocked\"]",
            json.encodeToString(DiySuitabilityPayload.values().toList()),
        )
        assertEquals(
            "[\"unknown\",\"beginner\",\"intermediate\",\"advanced\"]",
            json.encodeToString(UserSkillLevelPayload.values().toList()),
        )
    }

    private fun repositoryFor(envelope: PhaseReportEnvelope): DefaultReportRepository =
        DefaultReportRepository(FakeReportApiService(envelope), CoreModule.provideJson())

    private fun envelope(
        payload: String,
        schemaVersion: String = payloadSchemaVersion(payload),
    ): PhaseReportEnvelope =
        PhaseReportEnvelope(
            id = "report-1",
            repairSessionId = "session-1",
            type = "diagnostic",
            schemaVersion = schemaVersion,
            phase = "diagnostic",
            payload = CoreModule.provideJson().parseToJsonElement(payload),
            createdAt = "2026-07-10T12:00:00Z",
        )

    private fun payloadSchemaVersion(payload: String): String =
        CoreModule.provideJson()
            .parseToJsonElement(payload)
            .jsonObject["schema_version"]!!
            .toString()
            .trim('"')

    private inline fun <reified T : RepairReport> assertSuccess(result: ApiResult<RepairReport>): T {
        assertTrue(result is ApiResult.Success<*>)
        val report = (result as ApiResult.Success<*>).data
        assertTrue(report is T)
        @Suppress("UNCHECKED_CAST")
        return report as T
    }

    private fun assertVersionError(result: ApiResult<RepairReport>) {
        assertTrue(result is ApiResult.Error)
        assertEquals("Unsupported or mismatched report version.", (result as ApiResult.Error).message)
    }
}

private class FakeReportApiService(
    private val report: PhaseReportEnvelope,
) : BikeDocApiService {
    override suspend fun getMe(): UserProfile = error("unused")
    override suspend fun getBikes(limit: Int, cursor: String?): BikeListResponseDto = error("unused")
    override suspend fun createBike(bike: JsonObject): BikeProfileDto = error("unused")
    override suspend fun getBike(bikeId: String): BikeProfileDto = error("unused")
    override suspend fun updateBike(bikeId: String, bike: JsonObject): BikeProfileDto = error("unused")
    override suspend fun deleteBike(bikeId: String) = error("unused")
    override suspend fun getRepairSessions(
        bikeId: String,
        limit: Int?,
        cursor: String?,
    ): RepairSessionListResponse = error("unused")
    override suspend fun createRepairSession(body: RepairSessionCreate): RepairSession = error("unused")
    override suspend fun getRepairSession(sessionId: String): RepairSession = error("unused")
    override suspend fun createTurn(sessionId: String, body: TurnCreate): TurnAccepted = error("unused")
    override suspend fun uploadArtifact(
        file: MultipartBody.Part,
        purpose: RequestBody,
        repairSessionId: RequestBody,
        clientArtifactId: RequestBody?,
    ): ArtifactUploadResponse = error("unused")

    override suspend fun getReports(
        sessionId: String,
        limit: Int,
        cursor: String?,
    ): PhaseReportList = error("unused")
    override suspend fun getReport(sessionId: String, reportId: String): PhaseReportEnvelope = report
}

private fun v2SupportedPayload(): String =
    """
    {"schema_version":"diagnostic_report.v2","diagnostic_outcome":"diagnosis_supported",
    "reported_symptoms":["Chain skips under load."],
    "primary_diagnosis":{"component":"chain","issue":"Wear","confidence":"medium","diy_suitability":"caution","supporting_finding_ids":["finding-1"]},
    "contributing_factors":[{"component":"derailleur","issue":"Indexing is slightly out.","confidence":"low","evidence_summary":"Functional check supports it.","supporting_finding_ids":["finding-2"]}],
    "observed_findings":[{"finding_id":"finding-1","component":"chain","finding":"Wear measured.","evidence_source":"image","evidence_source_detail":null,"relationship_to_symptoms":"supports_primary_diagnosis","artifact_ids":["artifact-1"]}],
    "alternate_hypotheses":[{"component":"hub","issue":"Freehub engagement","confidence":"low","evidence_summary":"Still plausible.","supporting_finding_ids":["finding-1"]}],
    "unresolved_uncertainties":["Hanger alignment was not measured."],"evidence_summary":"Wear explains the symptom.","key_artifact_ids":["artifact-1"],"user_skill_level":"beginner","safety_flags":[],"diagnostic_session_id":"phase-session-1"}
    """.trimIndent()

private fun v2LimitedPayload(): String =
    """
    {"schema_version":"diagnostic_report.v2","diagnostic_outcome":"in_person_assessment_required",
    "reported_symptoms":["Front brake is weak."],"primary_diagnosis":null,"contributing_factors":[],
    "observed_findings":[{"finding_id":"finding-1","component":"front brake","finding":"User reports weak braking.","evidence_source":"user_report","evidence_source_detail":null,"relationship_to_symptoms":"unknown","artifact_ids":[]}],
    "alternate_hypotheses":[],"unresolved_uncertainties":[],"evidence_summary":"Inspection is required.","key_artifact_ids":[],"user_skill_level":"beginner","safety_flags":[],"diagnostic_session_id":"phase-session-1"}
    """.trimIndent()

private fun v1Payload(): String =
    """
    {"schema_version":"diagnostic_report.v1","primary_diagnosis":{"component":"derailleur","issue":"Bent hanger","confidence":"high"},"alternate_hypotheses":[{"component":"chain","issue":"Wear","confidence":"low","ruled_out_by":"Chain wear measurement"}],"evidence_summary":"The hanger is bent.","repair_estimate":{"difficulty":"medium","difficulty_notes":"Alignment required.","tools_required":[],"parts_required":[],"repair_time":{"low_minutes":30,"high_minutes":60},"shop_repair_cost":{"low_usd":80,"high_usd":120}},"key_artifact_ids":[],"user_skill_level":"beginner","safety_flags":[],"diagnostic_session_id":"phase-session-1"}
    """.trimIndent()
