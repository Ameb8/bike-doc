package com.bikedoc.android.sessions.report

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R
import com.bikedoc.android.api.AlternateHypothesis
import com.bikedoc.android.api.AlternateHypothesisV2
import com.bikedoc.android.api.ContributingFactor
import com.bikedoc.android.api.CostEstimate
import com.bikedoc.android.api.Diagnosis
import com.bikedoc.android.api.DiagnosisV2
import com.bikedoc.android.api.DiagnosticConfidence
import com.bikedoc.android.api.DiagnosticOutcome
import com.bikedoc.android.api.DiagnosticReport
import com.bikedoc.android.api.DiagnosticReportV2
import com.bikedoc.android.api.DiagnosticRelevance
import com.bikedoc.android.api.DiySuitability
import com.bikedoc.android.api.EvidenceSource
import com.bikedoc.android.api.ObservedFinding
import com.bikedoc.android.api.PartNeeded
import com.bikedoc.android.api.PlanCostEstimate
import com.bikedoc.android.api.PlanReport
import com.bikedoc.android.api.PriceListing
import com.bikedoc.android.api.PriceLookupResult
import com.bikedoc.android.api.RepairEstimate
import com.bikedoc.android.api.RepairReport
import com.bikedoc.android.api.RepairTimeEstimate
import com.bikedoc.android.api.ShopRepairCostEstimate
import com.bikedoc.android.api.ToolNeeded
import com.bikedoc.android.api.models.SafetyFlag
import java.util.Locale

@Composable
fun DiagnosticReportScreen(
    viewModel: DiagnosticReportViewModel,
    onNavigateBack: () -> Unit,
) {
    val state by viewModel.uiState.collectAsState()
    DiagnosticReportContent(
        state = state,
        onNavigateBack = onNavigateBack,
        onRetry = viewModel::retry,
    )
}

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun DiagnosticReportContent(
    state: DiagnosticReportUiState,
    onNavigateBack: () -> Unit,
    onRetry: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(text = stringResource(R.string.diagnostic_report_title)) },
                navigationIcon = {
                    TextButton(onClick = onNavigateBack) {
                        Text(text = stringResource(R.string.bike_edit_back))
                    }
                },
            )
        },
    ) { padding ->
        when {
            state.isLoading -> DiagnosticReportLoadingState(padding = padding)
            state.error != null ->
                DiagnosticReportErrorState(
                    padding = padding,
                    message = state.error,
                    onRetry = onRetry,
                )

            state.report != null ->
                DiagnosticReportLoadedState(
                    padding = padding,
                    report = state.report,
                )
        }
    }
}

@Composable
private fun DiagnosticReportLoadingState(padding: PaddingValues) {
    Box(
        modifier =
            Modifier
                .fillMaxSize()
                .padding(padding),
        contentAlignment = Alignment.Center,
    ) {
        CircularProgressIndicator()
    }
}

@Composable
private fun DiagnosticReportErrorState(
    padding: PaddingValues,
    message: String,
    onRetry: () -> Unit,
) {
    Column(
        modifier =
            Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = stringResource(R.string.diagnostic_report_load_error_title),
            style = MaterialTheme.typography.titleMedium,
        )
        Text(
            text = message,
            modifier = Modifier.padding(top = 8.dp),
            style = MaterialTheme.typography.bodyMedium,
        )
        Button(
            onClick = onRetry,
            modifier = Modifier.padding(top = 16.dp),
        ) {
            Text(text = stringResource(R.string.diagnostic_report_retry))
        }
    }
}

@Composable
private fun DiagnosticReportLoadedState(
    padding: PaddingValues,
    report: RepairReport,
) {
    Column(
        modifier =
            Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        if (report.safetyFlags.isNotEmpty()) {
            SafetySection(flags = report.safetyFlags)
        }
        when (report) {
            is DiagnosticReport -> DiagnosticReportSections(report = report)
            is DiagnosticReportV2 -> DiagnosticReportV2Sections(report = report)
            is PlanReport -> PlanReportSections(report = report)
        }
    }
}

@Composable
private fun DiagnosticReportSections(report: DiagnosticReport) {
    PrimaryDiagnosisSection(diagnosis = report.primaryDiagnosis)
    RepairEstimateSection(estimate = report.repairEstimate)
    report.costEstimate?.let { costEstimate ->
        DiagnosticCostSummarySection(costEstimate = costEstimate)
        PriceLookupSection(items = costEstimate.items)
    }
    ReportSection(title = stringResource(R.string.diagnostic_report_evidence_title)) {
        Text(
            text = report.evidenceSummary,
            style = MaterialTheme.typography.bodyLarge,
        )
    }
    if (report.alternateHypotheses.isNotEmpty()) {
        ReportSection(title = stringResource(R.string.diagnostic_report_alternates_title)) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                report.alternateHypotheses.forEach { hypothesis ->
                    AlternateHypothesisItem(hypothesis = hypothesis)
                }
            }
        }
    }
    ReportSection(title = stringResource(R.string.diagnostic_report_details_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            DetailRow(
                label = stringResource(R.string.diagnostic_report_skill_level_label),
                value = report.userSkillLevel.toDisplayLabel(),
            )
            if (report.keyArtifactIds.isNotEmpty()) {
                DetailRow(
                    label = stringResource(R.string.diagnostic_report_artifacts_label),
                    value = report.keyArtifactIds.size.toString(),
                )
            }
        }
    }
}

@Composable
private fun DiagnosticReportV2Sections(report: DiagnosticReportV2) {
    DiagnosticOutcomeSection(outcome = report.diagnosticOutcome)
    ReportSection(title = stringResource(R.string.diagnostic_report_symptoms_title)) {
        ReportList(items = report.reportedSymptoms)
    }
    report.primaryDiagnosis?.let { diagnosis ->
        PrimaryDiagnosisV2Section(diagnosis = diagnosis)
    } ?: LimitedDiagnosisSection(outcome = report.diagnosticOutcome)
    if (report.contributingFactors.isNotEmpty()) {
        ReportSection(title = stringResource(R.string.diagnostic_report_contributors_title)) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                report.contributingFactors.forEach { factor ->
                    ContributingFactorItem(factor = factor)
                }
            }
        }
    }
    ObservedFindingsSections(findings = report.observedFindings)
    if (report.alternateHypotheses.isNotEmpty()) {
        ReportSection(title = stringResource(R.string.diagnostic_report_alternates_title)) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                report.alternateHypotheses.forEach { hypothesis ->
                    AlternateHypothesisV2Item(hypothesis = hypothesis)
                }
            }
        }
    }
    if (report.unresolvedUncertainties.isNotEmpty()) {
        ReportSection(title = stringResource(R.string.diagnostic_report_uncertainties_title)) {
            ReportList(items = report.unresolvedUncertainties)
        }
    }
    ReportSection(title = stringResource(R.string.diagnostic_report_evidence_title)) {
        Text(text = report.evidenceSummary, style = MaterialTheme.typography.bodyLarge)
    }
    ReportSection(title = stringResource(R.string.diagnostic_report_details_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            DetailRow(
                label = stringResource(R.string.diagnostic_report_skill_level_label),
                value = report.userSkillLevel.toDisplayLabel(),
            )
            if (report.keyArtifactIds.isNotEmpty()) {
                DetailRow(
                    label = stringResource(R.string.diagnostic_report_artifacts_label),
                    value = report.keyArtifactIds.size.toString(),
                )
            }
        }
    }
}

@Composable
private fun DiagnosticOutcomeSection(outcome: DiagnosticOutcome) {
    ReportSection(title = stringResource(R.string.diagnostic_report_outcome_title)) {
        Text(text = outcome.toDisplayLabel(), style = MaterialTheme.typography.titleSmall)
        Text(text = outcome.toDescription(), style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun LimitedDiagnosisSection(outcome: DiagnosticOutcome) {
    ReportSection(title = stringResource(R.string.diagnostic_report_limited_title)) {
        Text(text = outcome.toLimitedDiagnosisMessage(), style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
private fun PrimaryDiagnosisV2Section(diagnosis: DiagnosisV2) {
    ReportSection(title = stringResource(R.string.diagnostic_report_primary_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(text = diagnosis.component, style = MaterialTheme.typography.titleMedium)
            Text(text = diagnosis.issue, style = MaterialTheme.typography.bodyLarge)
            DetailRow(
                label = stringResource(R.string.diagnostic_report_confidence_detail_label),
                value = diagnosis.confidence.toDisplayLabel(),
            )
            DetailRow(
                label = stringResource(R.string.diagnostic_report_diy_detail_label),
                value = diagnosis.diySuitability.toDisplayLabel(),
            )
        }
    }
}

@Composable
private fun ContributingFactorItem(factor: ContributingFactor) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(text = factor.component, style = MaterialTheme.typography.titleSmall)
        Text(text = factor.issue, style = MaterialTheme.typography.bodyMedium)
        DetailRow(
            label = stringResource(R.string.diagnostic_report_confidence_detail_label),
            value = factor.confidence.toDisplayLabel(),
        )
        Text(text = factor.evidenceSummary, style = MaterialTheme.typography.bodySmall)
        HorizontalDivider()
    }
}

@Composable
private fun ObservedFindingsSections(findings: List<ObservedFinding>) {
    val relevantFindings = findings.filterNot { it.relationshipToSymptoms == DiagnosticRelevance.INCIDENTAL }
    val incidentalFindings = findings.filter { it.relationshipToSymptoms == DiagnosticRelevance.INCIDENTAL }
    ReportSection(title = stringResource(R.string.diagnostic_report_findings_title)) {
        if (relevantFindings.isEmpty()) {
            Text(
                text = stringResource(R.string.diagnostic_report_no_relevant_findings),
                style = MaterialTheme.typography.bodyMedium,
            )
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                relevantFindings.forEach { finding -> ObservedFindingItem(finding = finding) }
            }
        }
    }
    if (incidentalFindings.isNotEmpty()) {
        ReportSection(title = stringResource(R.string.diagnostic_report_incidental_findings_title)) {
            Text(
                text = stringResource(R.string.diagnostic_report_incidental_findings_description),
                style = MaterialTheme.typography.bodyMedium,
            )
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                incidentalFindings.forEach { finding -> ObservedFindingItem(finding = finding) }
            }
        }
    }
}

@Composable
private fun ObservedFindingItem(finding: ObservedFinding) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(text = finding.component, style = MaterialTheme.typography.titleSmall)
        Text(text = finding.finding, style = MaterialTheme.typography.bodyMedium)
        DetailRow(
            label = stringResource(R.string.diagnostic_report_evidence_source_label),
            value = finding.evidenceSource.toDisplayLabel(finding.evidenceSourceDetail),
        )
        DetailRow(
            label = stringResource(R.string.diagnostic_report_relevance_label),
            value = finding.relationshipToSymptoms.toDisplayLabel(),
        )
        HorizontalDivider()
    }
}

@Composable
private fun AlternateHypothesisV2Item(hypothesis: AlternateHypothesisV2) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(text = hypothesis.component, style = MaterialTheme.typography.titleSmall)
        Text(text = hypothesis.issue, style = MaterialTheme.typography.bodyMedium)
        DetailRow(
            label = stringResource(R.string.diagnostic_report_confidence_detail_label),
            value = hypothesis.confidence.toDisplayLabel(),
        )
        Text(text = hypothesis.evidenceSummary, style = MaterialTheme.typography.bodySmall)
        HorizontalDivider()
    }
}

@Composable
private fun ReportList(items: List<String>) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        items.forEach { item ->
            Text(
                text = stringResource(R.string.diagnostic_report_list_item, item),
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

@Composable
private fun DiagnosticCostSummarySection(costEstimate: PlanCostEstimate) {
    ReportSection(title = stringResource(R.string.diagnostic_report_cost_summary_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            DetailRow(
                label = stringResource(R.string.diagnostic_report_parts_total_label),
                value = costEstimate.partsTotal.toDisplayPrice(),
            )
            DetailRow(
                label = stringResource(R.string.diagnostic_report_tools_total_label),
                value = costEstimate.toolsTotal.toDisplayPrice(),
            )
            DetailRow(
                label = stringResource(R.string.diagnostic_report_diy_total_label),
                value = costEstimate.diyTotal.toDisplayPrice(),
            )
            Text(
                text = stringResource(R.string.diagnostic_report_pricing_disclaimer),
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun PlanReportSections(report: PlanReport) {
    ReportSection(title = stringResource(R.string.diagnostic_report_plan_summary_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                text = report.diagnosisSummary,
                style = MaterialTheme.typography.bodyLarge,
            )
            DetailRow(
                label = stringResource(R.string.diagnostic_report_recommendation_label),
                value = report.recommendation.toDisplayLabel(),
            )
            Text(
                text = report.recommendationBasis,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
    CostSummarySection(report = report)
    RequirementsSection(
        parts = report.partsNeeded,
        tools = report.toolsNeeded,
    )
    PriceLookupSection(items = report.costEstimate.items)
}

@Composable
private fun CostSummarySection(report: PlanReport) {
    ReportSection(title = stringResource(R.string.diagnostic_report_cost_summary_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            DetailRow(
                label = stringResource(R.string.diagnostic_report_parts_total_label),
                value = report.costEstimate.partsTotal.toDisplayPrice(),
            )
            DetailRow(
                label = stringResource(R.string.diagnostic_report_tools_total_label),
                value = report.costEstimate.toolsTotal.toDisplayPrice(),
            )
            DetailRow(
                label = stringResource(R.string.diagnostic_report_diy_total_label),
                value = report.costEstimate.diyTotal.toDisplayPrice(),
            )
            DetailRow(
                label = stringResource(R.string.diagnostic_report_shop_total_label),
                value = report.shopEstimate.toDisplayPrice(),
            )
            Text(
                text = stringResource(R.string.diagnostic_report_pricing_disclaimer),
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun RequirementsSection(
    parts: List<PartNeeded>,
    tools: List<ToolNeeded>,
) {
    ReportSection(title = stringResource(R.string.diagnostic_report_requirements_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
            RequirementGroup(
                title = stringResource(R.string.diagnostic_report_parts_required_label),
                rows =
                    parts.map { part ->
                        listOfNotNull(
                            part.item,
                            part.specification?.takeIf(String::isNotBlank),
                            stringResource(R.string.diagnostic_report_quantity_label, part.quantity),
                            part.estimatedPrice?.toDisplayPrice(),
                        ).joinToString(" - ")
                    },
            )
            RequirementGroup(
                title = stringResource(R.string.diagnostic_report_tools_required_label),
                rows =
                    tools.map { tool ->
                        listOfNotNull(
                            tool.item,
                            tool.action.toDisplayLabel(),
                            stringResource(R.string.diagnostic_report_quantity_label, tool.quantity),
                            tool.estimatedPrice?.toDisplayPrice(),
                            tool.notes?.takeIf(String::isNotBlank),
                        ).joinToString(" - ")
                    },
            )
        }
    }
}

@Composable
private fun RequirementGroup(
    title: String,
    rows: List<String>,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            text = title,
            style = MaterialTheme.typography.titleSmall,
        )
        if (rows.isEmpty()) {
            Text(
                text = stringResource(R.string.diagnostic_report_none_predicted),
                style = MaterialTheme.typography.bodyMedium,
            )
        } else {
            rows.forEach { row ->
                Text(
                    text = stringResource(R.string.diagnostic_report_list_item, row),
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

@Composable
private fun PriceLookupSection(items: List<PriceLookupResult>) {
    ReportSection(title = stringResource(R.string.diagnostic_report_price_lookup_title)) {
        if (items.isEmpty()) {
            Text(
                text = stringResource(R.string.diagnostic_report_no_price_lookup),
                style = MaterialTheme.typography.bodyMedium,
            )
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                items.forEach { item ->
                    PriceLookupItem(item = item)
                    HorizontalDivider()
                }
            }
        }
    }
}

@Composable
private fun PriceLookupItem(item: PriceLookupResult) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            text = item.requirementName,
            style = MaterialTheme.typography.titleSmall,
        )
        DetailRow(
            label = stringResource(R.string.diagnostic_report_type_label),
            value = item.itemType.toDisplayLabel(),
        )
        DetailRow(
            label = stringResource(R.string.diagnostic_report_status_label),
            value = item.status.toDisplayLabel(),
        )
        DetailRow(
            label = stringResource(R.string.diagnostic_report_confidence_detail_label),
            value = item.estimateConfidence.toDisplayLabel(),
        )
        item.estimatedPrice?.let { estimate ->
            DetailRow(
                label = stringResource(R.string.diagnostic_report_estimated_range_label),
                value = estimate.toDisplayPrice(),
            )
        }
        item.primaryListing?.let { listing ->
            ListingItem(
                title = stringResource(R.string.diagnostic_report_primary_listing_label),
                listing = listing,
            )
        }
        item.alternateListings.forEach { listing ->
            ListingItem(
                title = stringResource(R.string.diagnostic_report_alternate_listing_label),
                listing = listing,
            )
        }
        val flags = item.toUncertaintyFlags()
        if (flags.isNotEmpty()) {
            Text(
                text = flags.joinToString(" - "),
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Text(
            text = stringResource(R.string.diagnostic_report_last_checked_label, item.lookedUpAt.toDisplayDate()),
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun ListingItem(
    title: String,
    listing: PriceListing,
) {
    val context = LocalContext.current
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            text = title,
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = listing.title,
            style = MaterialTheme.typography.bodyMedium,
        )
        DetailRow(
            label = stringResource(R.string.diagnostic_report_retailer_label),
            value = listing.retailer,
        )
        DetailRow(
            label = stringResource(R.string.diagnostic_report_observed_price_label),
            value = listing.observedPrice.toMoney(listing.currency),
        )
        Text(
            text = listing.matchRationale,
            style = MaterialTheme.typography.bodySmall,
        )
        ElevatedButton(
            onClick = {
                runCatching {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(listing.url)))
                }
            },
            modifier = Modifier.widthIn(max = 220.dp),
        ) {
            Text(text = stringResource(R.string.diagnostic_report_open_listing))
        }
    }
}

@Composable
private fun SafetySection(flags: List<SafetyFlag>) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
        tonalElevation = 2.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = stringResource(R.string.diagnostic_report_safety_title),
                style = MaterialTheme.typography.titleMedium,
            )
            flags.forEach { flag ->
                Text(
                    text = flag.message,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

@Composable
private fun PrimaryDiagnosisSection(diagnosis: Diagnosis) {
    ReportSection(title = stringResource(R.string.diagnostic_report_primary_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(
                text = diagnosis.component,
                style = MaterialTheme.typography.titleMedium,
            )
            Text(
                text = diagnosis.issue,
                style = MaterialTheme.typography.bodyLarge,
            )
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                DetailRow(
                    label = stringResource(R.string.diagnostic_report_confidence_detail_label),
                    value = diagnosis.confidence.toDisplayLabel(),
                )
                DetailRow(
                    label = stringResource(R.string.diagnostic_report_diy_detail_label),
                    value = diagnosis.diySuitability.toDisplayLabel(),
                )
            }
        }
    }
}

@Composable
private fun RepairEstimateSection(estimate: RepairEstimate) {
    ReportSection(title = stringResource(R.string.diagnostic_report_repair_estimate_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                DetailRow(
                    label = stringResource(R.string.diagnostic_report_difficulty_label),
                    value = estimate.difficulty.toDisplayLabel(),
                )
                DetailRow(
                    label = stringResource(R.string.diagnostic_report_repair_time_label),
                    value =
                        stringResource(
                            R.string.diagnostic_report_repair_time_value,
                            estimate.repairTime.lowMinutes,
                            estimate.repairTime.highMinutes,
                        ),
                )
                DetailRow(
                    label = stringResource(R.string.diagnostic_report_shop_cost_label),
                    value =
                        stringResource(
                            R.string.diagnostic_report_shop_cost_value,
                            estimate.shopRepairCost.lowUsd,
                            estimate.shopRepairCost.highUsd,
                        ),
                )
            }
            Text(
                text = estimate.difficultyNotes,
                style = MaterialTheme.typography.bodyMedium,
            )
            estimate.shopRepairCost.notes?.takeIf(String::isNotBlank)?.let { notes ->
                Text(
                    text = notes,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            EstimateList(
                title = stringResource(R.string.diagnostic_report_tools_required_label),
                items = estimate.toolsRequired,
            )
            EstimateList(
                title = stringResource(R.string.diagnostic_report_parts_required_label),
                items = estimate.partsRequired,
            )
        }
    }
}

@Composable
private fun EstimateList(
    title: String,
    items: List<String>,
) {
    RequirementGroup(title = title, rows = items)
}

@Composable
private fun AlternateHypothesisItem(hypothesis: AlternateHypothesis) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            text = hypothesis.component,
            style = MaterialTheme.typography.titleSmall,
        )
        Text(
            text = hypothesis.issue,
            style = MaterialTheme.typography.bodyMedium,
        )
        Text(
            text =
                stringResource(
                    R.string.diagnostic_report_confidence_label,
                    hypothesis.confidence.toDisplayLabel(),
                ),
            style = MaterialTheme.typography.bodySmall,
        )
        hypothesis.ruledOutBy?.takeIf(String::isNotBlank)?.let { ruledOutBy ->
            Text(
                text =
                    stringResource(
                        R.string.diagnostic_report_ruled_out_label,
                        ruledOutBy,
                    ),
                style = MaterialTheme.typography.bodySmall,
            )
        }
        HorizontalDivider()
    }
}

@Composable
private fun ReportSection(
    title: String,
    content: @Composable () -> Unit,
) {
    Surface(
        tonalElevation = 1.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
            )
            content()
        }
    }
}

@Composable
private fun DetailRow(
    label: String,
    value: String,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = label,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodyMedium,
        )
        Text(
            text = value,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun PriceLookupResult.toUncertaintyFlags(): List<String> =
    listOfNotNull(
        stringResource(R.string.diagnostic_report_compatibility_uncertain).takeIf { compatibilityUncertain },
        stringResource(R.string.diagnostic_report_search_ambiguous).takeIf { searchMatchAmbiguous },
        stringResource(R.string.diagnostic_report_generic_substitute).takeIf { genericSubstituteUsed },
        stringResource(R.string.diagnostic_report_exact_match_unconfirmed).takeIf { exactMatchNotConfirmed },
    )

private fun CostEstimate.toDisplayPrice(): String =
    when {
        minAmount == null && maxAmount == null -> confidence.toDisplayLabel()
        minAmount == null -> stringResourceMoney(maxAmount, currency)
        maxAmount == null -> stringResourceMoney(minAmount, currency)
        minAmount == maxAmount -> stringResourceMoney(minAmount, currency)
        else -> "${stringResourceMoney(minAmount, currency)}-${stringResourceMoney(maxAmount, currency)}"
    }

private fun stringResourceMoney(
    amount: Double?,
    currency: String,
): String = amount?.toMoney(currency) ?: currency

private fun Double.toMoney(currency: String): String =
    if (currency == "USD") {
        "$" + String.format(Locale.US, "%.2f", this)
    } else {
        currency + " " + String.format(Locale.US, "%.2f", this)
    }

private fun String.toDisplayDate(): String = substringBefore("T").ifBlank { this }

private fun String.toDisplayLabel(): String =
    split("_")
        .filter(String::isNotBlank)
        .joinToString(" ") { word -> word.replaceFirstChar(Char::uppercaseChar) }

@Preview(showBackground = true, widthDp = 360, heightDp = 1200)
@Composable
private fun DiagnosticReportV2SupportedPreview() {
    DiagnosticReportContent(
        state = DiagnosticReportUiState(report = previewV2SupportedReport),
        onNavigateBack = {},
        onRetry = {},
    )
}

@Preview(showBackground = true, widthDp = 360, heightDp = 900)
@Composable
private fun DiagnosticReportV2LimitedPreview() {
    DiagnosticReportContent(
        state = DiagnosticReportUiState(report = previewV2LimitedReport),
        onNavigateBack = {},
        onRetry = {},
    )
}

@Preview(showBackground = true, widthDp = 360, heightDp = 900)
@Composable
private fun DiagnosticReportV1Preview() {
    DiagnosticReportContent(
        state = DiagnosticReportUiState(report = previewV1Report),
        onNavigateBack = {},
        onRetry = {},
    )
}

private val previewV2SupportedReport =
    DiagnosticReportV2(
        id = "preview-v2",
        createdAt = "",
        diagnosticOutcome = DiagnosticOutcome.DIAGNOSIS_SUPPORTED,
        reportedSymptoms = listOf("Chain skips under load.", "Shifting hesitates in small sprockets."),
        primaryDiagnosis =
            DiagnosisV2(
                component = "Chain and cassette",
                issue = "Wear is causing poor engagement under load.",
                confidence = DiagnosticConfidence.MEDIUM,
                diySuitability = DiySuitability.CAUTION,
                supportingFindingIds = listOf("chain-wear"),
            ),
        contributingFactors =
            listOf(
                ContributingFactor(
                    component = "Rear derailleur",
                    issue = "Indexing is slightly out.",
                    confidence = DiagnosticConfidence.MEDIUM,
                    evidenceSummary = "A functional check found delayed alignment in affected gears.",
                    supportingFindingIds = listOf("indexing"),
                ),
                ContributingFactor(
                    component = "Shift cable",
                    issue = "Cable friction is worsening the delayed shift.",
                    confidence = DiagnosticConfidence.LOW,
                    evidenceSummary = "Cable movement was inconsistent during the functional check.",
                    supportingFindingIds = listOf("cable"),
                ),
            ),
        observedFindings =
            listOf(
                ObservedFinding(
                    findingId = "chain-wear",
                    component = "Chain",
                    finding = "A chain checker indicates wear beyond the replacement threshold.",
                    evidenceSource = EvidenceSource.MEASUREMENT,
                    evidenceSourceDetail = null,
                    relationshipToSymptoms = DiagnosticRelevance.SUPPORTS_PRIMARY_DIAGNOSIS,
                    artifactIds = emptyList(),
                ),
                ObservedFinding(
                    findingId = "indexing",
                    component = "Rear derailleur",
                    finding = "A functional check showed delayed alignment in the affected gears.",
                    evidenceSource = EvidenceSource.FUNCTIONAL_CHECK,
                    evidenceSourceDetail = null,
                    relationshipToSymptoms = DiagnosticRelevance.SUPPORTED_CONTRIBUTOR,
                    artifactIds = emptyList(),
                ),
                ObservedFinding(
                    findingId = "tire",
                    component = "Rear tire",
                    finding = "Small cracks are visible in the sidewall.",
                    evidenceSource = EvidenceSource.IMAGE,
                    evidenceSourceDetail = null,
                    relationshipToSymptoms = DiagnosticRelevance.INCIDENTAL,
                    artifactIds = listOf("photo-1"),
                ),
            ),
        alternateHypotheses =
            listOf(
                AlternateHypothesisV2(
                    component = "Freehub",
                    issue = "Intermittent engagement could also produce skipping under load.",
                    confidence = DiagnosticConfidence.LOW,
                    evidenceSummary =
                        "It remains plausible, but the wear measurement and gear-specific behavior favor the primary diagnosis.",
                    supportingFindingIds = listOf("chain-wear"),
                ),
            ),
        unresolvedUncertainties = listOf("Hanger alignment was not physically measured."),
        evidenceSummary =
            "The measurement supports drivetrain wear as the primary cause; indexing and cable friction also contribute.",
        keyArtifactIds = listOf("photo-1"),
        userSkillLevel = com.bikedoc.android.api.UserSkillLevel.BEGINNER,
        safetyFlags =
            listOf(
                SafetyFlag(
                    code = "tire_damage",
                    severity = "warning",
                    phase = "diagnostic",
                    message = "Have the cracked tire inspected before riding at speed.",
                    blocksRepairInstructions = false,
                ),
            ),
        diagnosticSessionId = "preview-session",
    )

private val previewV2LimitedReport =
    DiagnosticReportV2(
        id = "preview-v2-limited",
        createdAt = "",
        diagnosticOutcome = DiagnosticOutcome.IN_PERSON_ASSESSMENT_REQUIRED,
        reportedSymptoms = listOf("Front brake feels weak."),
        primaryDiagnosis = null,
        contributingFactors = emptyList(),
        observedFindings =
            listOf(
                ObservedFinding(
                    findingId = "weak-brake",
                    component = "Front brake",
                    finding = "The user reports weak braking.",
                    evidenceSource = EvidenceSource.USER_REPORT,
                    evidenceSourceDetail = null,
                    relationshipToSymptoms = DiagnosticRelevance.UNKNOWN,
                    artifactIds = emptyList(),
                ),
            ),
        alternateHypotheses = emptyList(),
        unresolvedUncertainties = emptyList(),
        evidenceSummary = "A remote assessment could not safely determine why braking is weak.",
        keyArtifactIds = emptyList(),
        userSkillLevel = com.bikedoc.android.api.UserSkillLevel.BEGINNER,
        safetyFlags = emptyList(),
        diagnosticSessionId = "preview-session",
    )

private val previewV1Report =
    DiagnosticReport(
        id = "preview-v1",
        createdAt = "",
        primaryDiagnosis =
            Diagnosis(
                component = "Rear derailleur",
                issue = "Bent hanger",
                confidence = "high",
                diySuitability = "caution",
            ),
        alternateHypotheses = emptyList(),
        evidenceSummary = "The hanger is bent.",
        repairEstimate =
            RepairEstimate(
                difficulty = "medium",
                difficultyNotes = "Alignment is required.",
                toolsRequired = listOf("Derailleur hanger alignment gauge"),
                partsRequired = emptyList(),
                repairTime = RepairTimeEstimate(lowMinutes = 30, highMinutes = 60),
                shopRepairCost = ShopRepairCostEstimate(lowUsd = 80, highUsd = 120, notes = null),
            ),
        userSkillLevel = "beginner",
        safetyFlags = emptyList(),
        keyArtifactIds = emptyList(),
        costEstimate = null,
    )

@Composable
private fun DiagnosticOutcome.toDisplayLabel(): String =
    stringResource(
        when (this) {
            DiagnosticOutcome.DIAGNOSIS_SUPPORTED -> R.string.diagnostic_report_outcome_diagnosis_supported
            DiagnosticOutcome.USER_DECLINED_MORE_INPUT -> R.string.diagnostic_report_outcome_user_declined
            DiagnosticOutcome.REQUESTED_INPUT_UNAVAILABLE -> R.string.diagnostic_report_outcome_input_unavailable
            DiagnosticOutcome.IN_PERSON_ASSESSMENT_REQUIRED -> R.string.diagnostic_report_outcome_in_person
        },
    )

@Composable
private fun DiagnosticOutcome.toDescription(): String =
    stringResource(
        when (this) {
            DiagnosticOutcome.DIAGNOSIS_SUPPORTED ->
                R.string.diagnostic_report_outcome_diagnosis_supported_description
            DiagnosticOutcome.USER_DECLINED_MORE_INPUT ->
                R.string.diagnostic_report_outcome_user_declined_description
            DiagnosticOutcome.REQUESTED_INPUT_UNAVAILABLE ->
                R.string.diagnostic_report_outcome_input_unavailable_description
            DiagnosticOutcome.IN_PERSON_ASSESSMENT_REQUIRED ->
                R.string.diagnostic_report_outcome_in_person_description
        },
    )

@Composable
private fun DiagnosticOutcome.toLimitedDiagnosisMessage(): String =
    stringResource(
        when (this) {
            DiagnosticOutcome.USER_DECLINED_MORE_INPUT -> R.string.diagnostic_report_limited_user_declined
            DiagnosticOutcome.REQUESTED_INPUT_UNAVAILABLE -> R.string.diagnostic_report_limited_input_unavailable
            DiagnosticOutcome.IN_PERSON_ASSESSMENT_REQUIRED -> R.string.diagnostic_report_limited_in_person
            DiagnosticOutcome.DIAGNOSIS_SUPPORTED -> R.string.diagnostic_report_limited_generic
        },
    )

@Composable
private fun DiagnosticConfidence.toDisplayLabel(): String =
    stringResource(
        when (this) {
            DiagnosticConfidence.LOW -> R.string.diagnostic_report_confidence_low
            DiagnosticConfidence.MEDIUM -> R.string.diagnostic_report_confidence_medium
            DiagnosticConfidence.HIGH -> R.string.diagnostic_report_confidence_high
        },
    )

@Composable
private fun DiySuitability.toDisplayLabel(): String =
    stringResource(
        when (this) {
            DiySuitability.UNKNOWN -> R.string.diagnostic_report_diy_unknown
            DiySuitability.REASONABLE -> R.string.diagnostic_report_diy_reasonable
            DiySuitability.CAUTION -> R.string.diagnostic_report_diy_caution
            DiySuitability.SHOP_RECOMMENDED -> R.string.diagnostic_report_diy_shop_recommended
            DiySuitability.BLOCKED -> R.string.diagnostic_report_diy_blocked
        },
    )

@Composable
private fun com.bikedoc.android.api.UserSkillLevel.toDisplayLabel(): String =
    stringResource(
        when (this) {
            com.bikedoc.android.api.UserSkillLevel.UNKNOWN -> R.string.diagnostic_report_skill_unknown
            com.bikedoc.android.api.UserSkillLevel.BEGINNER -> R.string.diagnostic_report_skill_beginner
            com.bikedoc.android.api.UserSkillLevel.INTERMEDIATE -> R.string.diagnostic_report_skill_intermediate
            com.bikedoc.android.api.UserSkillLevel.ADVANCED -> R.string.diagnostic_report_skill_advanced
        },
    )

@Composable
private fun EvidenceSource.toDisplayLabel(detail: String?): String =
    when (this) {
        EvidenceSource.IMAGE -> stringResource(R.string.diagnostic_report_evidence_source_image)
        EvidenceSource.USER_REPORT -> stringResource(R.string.diagnostic_report_evidence_source_user_report)
        EvidenceSource.MEASUREMENT -> stringResource(R.string.diagnostic_report_evidence_source_measurement)
        EvidenceSource.FUNCTIONAL_CHECK -> stringResource(R.string.diagnostic_report_evidence_source_functional_check)
        EvidenceSource.REPAIR_HISTORY -> stringResource(R.string.diagnostic_report_evidence_source_repair_history)
        EvidenceSource.OTHER ->
            stringResource(
                R.string.diagnostic_report_evidence_source_other,
                detail.orEmpty(),
            )
    }

@Composable
private fun DiagnosticRelevance.toDisplayLabel(): String =
    stringResource(
        when (this) {
            DiagnosticRelevance.UNKNOWN -> R.string.diagnostic_report_relevance_unknown
            DiagnosticRelevance.POSSIBLE_CONTRIBUTOR ->
                R.string.diagnostic_report_relevance_possible_contributor
            DiagnosticRelevance.SUPPORTS_PRIMARY_DIAGNOSIS ->
                R.string.diagnostic_report_relevance_supports_primary
            DiagnosticRelevance.SUPPORTED_CONTRIBUTOR ->
                R.string.diagnostic_report_relevance_supported_contributor
            DiagnosticRelevance.INCIDENTAL -> R.string.diagnostic_report_relevance_incidental
        },
    )
