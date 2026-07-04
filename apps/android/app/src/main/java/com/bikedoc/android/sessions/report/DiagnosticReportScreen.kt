package com.bikedoc.android.sessions.report

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R
import com.bikedoc.android.api.AlternateHypothesis
import com.bikedoc.android.api.Diagnosis
import com.bikedoc.android.api.DiagnosticReport
import com.bikedoc.android.api.models.SafetyFlag

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
    report: DiagnosticReport,
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
        PrimaryDiagnosisSection(diagnosis = report.primaryDiagnosis)
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
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
        )
        Text(
            text = value,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

private fun String.toDisplayLabel(): String =
    split("_")
        .filter(String::isNotBlank)
        .joinToString(" ") { word -> word.replaceFirstChar(Char::uppercaseChar) }
