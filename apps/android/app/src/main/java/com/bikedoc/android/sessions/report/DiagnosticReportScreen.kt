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
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R
import com.bikedoc.android.api.AlternateHypothesis
import com.bikedoc.android.api.CostEstimate
import com.bikedoc.android.api.Diagnosis
import com.bikedoc.android.api.DiagnosticReport
import com.bikedoc.android.api.PartNeeded
import com.bikedoc.android.api.PlanCostEstimate
import com.bikedoc.android.api.PlanReport
import com.bikedoc.android.api.PriceListing
import com.bikedoc.android.api.PriceLookupResult
import com.bikedoc.android.api.RepairEstimate
import com.bikedoc.android.api.RepairReport
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
