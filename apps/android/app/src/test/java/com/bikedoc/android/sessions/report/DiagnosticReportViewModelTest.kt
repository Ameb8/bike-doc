package com.bikedoc.android.sessions.report

import androidx.lifecycle.SavedStateHandle
import com.bikedoc.android.MainDispatcherRule
import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.Diagnosis
import com.bikedoc.android.api.DiagnosisV2
import com.bikedoc.android.api.DiagnosticConfidence
import com.bikedoc.android.api.DiagnosticOutcome
import com.bikedoc.android.api.DiagnosticReport
import com.bikedoc.android.api.DiagnosticReportV2
import com.bikedoc.android.api.DiySuitability
import com.bikedoc.android.api.RepairEstimate
import com.bikedoc.android.api.RepairReport
import com.bikedoc.android.api.RepairTimeEstimate
import com.bikedoc.android.api.ReportRepository
import com.bikedoc.android.api.ShopRepairCostEstimate
import com.bikedoc.android.api.UserSkillLevel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class DiagnosticReportViewModelTest {
    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `loads a supported V2 report for presentation`() = runTest {
        val report = supportedV2Report()

        val viewModel = viewModelFor(report)

        assertEquals(report, viewModel.uiState.value.report)
        assertFalse(viewModel.uiState.value.isLoading)
        assertNull(viewModel.uiState.value.error)
    }

    @Test
    fun `loads a limited V2 report with a null primary diagnosis`() = runTest {
        val report = supportedV2Report().copy(
            diagnosticOutcome = DiagnosticOutcome.IN_PERSON_ASSESSMENT_REQUIRED,
            primaryDiagnosis = null,
            contributingFactors = emptyList(),
            alternateHypotheses = emptyList(),
            unresolvedUncertainties = emptyList(),
        )

        val viewModel = viewModelFor(report)

        val loaded = viewModel.uiState.value.report as DiagnosticReportV2
        assertNull(loaded.primaryDiagnosis)
        assertEquals(DiagnosticOutcome.IN_PERSON_ASSESSMENT_REQUIRED, loaded.diagnosticOutcome)
        assertTrue(loaded.contributingFactors.isEmpty())
    }

    @Test
    fun `loads V1 reports without changing their estimate data`() = runTest {
        val report =
            DiagnosticReport(
                id = "report-1",
                createdAt = "2026-07-31T12:00:00Z",
                primaryDiagnosis = Diagnosis("derailleur", "Bent hanger", "high", "caution"),
                alternateHypotheses = emptyList(),
                evidenceSummary = "The hanger is bent.",
                repairEstimate =
                    RepairEstimate(
                        difficulty = "medium",
                        difficultyNotes = "Alignment required.",
                        toolsRequired = emptyList(),
                        partsRequired = emptyList(),
                        repairTime = RepairTimeEstimate(30, 60),
                        shopRepairCost = ShopRepairCostEstimate(80, 120, null),
                    ),
                userSkillLevel = "beginner",
                safetyFlags = emptyList(),
                keyArtifactIds = emptyList(),
                costEstimate = null,
            )

        val viewModel = viewModelFor(report)

        val loaded = viewModel.uiState.value.report as DiagnosticReport
        assertEquals(30, loaded.repairEstimate.repairTime.lowMinutes)
        assertEquals(120, loaded.repairEstimate.shopRepairCost.highUsd)
    }

    private fun viewModelFor(report: RepairReport): DiagnosticReportViewModel =
        DiagnosticReportViewModel(
            reportRepository = FakeReportRepository(ApiResult.Success(report)),
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "session-1", "reportId" to "report-1")),
        )

    private fun supportedV2Report(): DiagnosticReportV2 =
        DiagnosticReportV2(
            id = "report-1",
            createdAt = "2026-07-31T12:00:00Z",
            diagnosticOutcome = DiagnosticOutcome.DIAGNOSIS_SUPPORTED,
            reportedSymptoms = listOf("Chain skips under load."),
            primaryDiagnosis =
                DiagnosisV2(
                    component = "chain",
                    issue = "Wear",
                    confidence = DiagnosticConfidence.MEDIUM,
                    diySuitability = DiySuitability.CAUTION,
                    supportingFindingIds = listOf("finding-1"),
                ),
            contributingFactors = emptyList(),
            observedFindings = emptyList(),
            alternateHypotheses = emptyList(),
            unresolvedUncertainties = emptyList(),
            evidenceSummary = "Wear explains the symptom.",
            keyArtifactIds = emptyList(),
            userSkillLevel = UserSkillLevel.BEGINNER,
            safetyFlags = emptyList(),
            diagnosticSessionId = "phase-session-1",
        )

    private class FakeReportRepository(
        private val result: ApiResult<RepairReport>,
    ) : ReportRepository {
        override suspend fun getDiagnosticReport(
            sessionId: String,
            reportId: String,
        ): ApiResult<RepairReport> = result
    }
}
