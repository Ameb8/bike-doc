package com.bikedoc.android.home

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R

@Composable
fun HomeScreen(viewModel: HomeViewModel) {
    val uiState by viewModel.uiState.collectAsState()
    HomeContent(
        state = uiState,
        onMyBikes = { viewModel.openBikes(selectionMode = false) },
        onEditBikeProfile = viewModel::openSelectedBikeProfile,
        onSelectRepairBike = viewModel::selectRepairBike,
        onStartSetup = viewModel::startSetup,
        onCloseSetup = viewModel::closeSetup,
        onStartingDetailChanged = viewModel::onStartingDetailChanged,
        onStartRepair = viewModel::startRepair,
        onResumeRepair = viewModel::openResumeRepair,
        onSignOut = viewModel::signOut,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
@Suppress("LongMethod")
private fun HomeContent(
    state: HomeUiState,
    onMyBikes: () -> Unit,
    onEditBikeProfile: () -> Unit,
    onSelectRepairBike: (String?) -> Unit,
    onStartSetup: () -> Unit,
    onCloseSetup: () -> Unit,
    onStartingDetailChanged: (String) -> Unit,
    onStartRepair: () -> Unit,
    onResumeRepair: () -> Unit,
    onSignOut: () -> Unit,
) {
    val menuExpanded = remember { mutableStateOf(false) }
    val bikeSelectorExpanded = remember { mutableStateOf(false) }
    val selectedBike = state.bikes.firstOrNull { it.id == state.selectedBikeId }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(text = stringResource(R.string.home_title), fontWeight = FontWeight.SemiBold) },
                actions = {
                    TextButton(onClick = { menuExpanded.value = true }) {
                        Text(
                            text = stringResource(R.string.home_menu_overflow),
                            style = MaterialTheme.typography.headlineSmall,
                        )
                    }
                    DropdownMenu(
                        expanded = menuExpanded.value,
                        onDismissRequest = { menuExpanded.value = false },
                    ) {
                        DropdownMenuItem(
                            text = { Text(text = stringResource(R.string.home_sign_out)) },
                            onClick = {
                                menuExpanded.value = false
                                onSignOut()
                            },
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surface),
            )
        },
    ) { padding ->
        Column(
            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 20.dp, vertical = 22.dp),
        ) {
            if (!state.isLoading && state.displayName != null) {
                Text(
                    text = stringResource(R.string.home_ready_eyebrow),
                    color = MaterialTheme.colorScheme.secondary,
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = stringResource(R.string.home_greeting, state.displayName),
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = stringResource(R.string.home_intro),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(20.dp))
            }

            state.error?.let {
                Text(text = it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(12.dp))
            }

            if (!state.isLoading && state.error == null) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(18.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(15.dp),
                        verticalAlignment = Alignment.Bottom,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                text = stringResource(R.string.home_working_on),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = FontWeight.Bold,
                            )
                            Spacer(Modifier.height(7.dp))
                            Surface(
                                modifier = Modifier.fillMaxWidth().clickable { bikeSelectorExpanded.value = true },
                                shape = RoundedCornerShape(12.dp),
                                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 13.dp, vertical = 14.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Text(
                                        text = selectedBike?.name ?: stringResource(R.string.home_new_bike),
                                        modifier = Modifier.weight(1f),
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                        style = MaterialTheme.typography.bodyMedium,
                                        fontWeight = FontWeight.SemiBold,
                                    )
                                    Text(text = "⌄", color = MaterialTheme.colorScheme.primary)
                                }
                            }
                            DropdownMenu(
                                expanded = bikeSelectorExpanded.value,
                                onDismissRequest = { bikeSelectorExpanded.value = false },
                            ) {
                                DropdownMenuItem(
                                    text = { Text(stringResource(R.string.home_new_bike)) },
                                    onClick = {
                                        bikeSelectorExpanded.value = false
                                        onSelectRepairBike(null)
                                    },
                                )
                                state.bikes.forEach { bike ->
                                    DropdownMenuItem(
                                        text = { Text(bike.name) },
                                        onClick = {
                                            bikeSelectorExpanded.value = false
                                            onSelectRepairBike(bike.id)
                                        },
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.width(4.dp))
                        TextButton(onClick = onMyBikes) { Text(stringResource(R.string.home_my_bikes)) }
                    }
                }

                Spacer(Modifier.height(16.dp))
                if (state.isSetupExpanded) {
                    StartSessionCard(
                        startingDetail = state.startingDetail,
                        isStartingRepair = state.isStartingRepair,
                        onClose = onCloseSetup,
                        onStartingDetailChanged = onStartingDetailChanged,
                        onStartRepair = onStartRepair,
                    )
                } else {
                    Button(
                        onClick = onStartSetup,
                        modifier = Modifier.fillMaxWidth().height(54.dp).shadow(4.dp, RoundedCornerShape(16.dp)),
                        shape = RoundedCornerShape(16.dp),
                    ) { Text(stringResource(R.string.home_start_with_bike_doc)) }
                    Spacer(Modifier.height(9.dp))
                    OutlinedButton(
                        onClick = onResumeRepair,
                        modifier = Modifier.fillMaxWidth().height(48.dp),
                        shape = RoundedCornerShape(16.dp),
                    ) { Text(stringResource(R.string.home_resume_session)) }
                }

                Spacer(Modifier.height(8.dp))
                TextButton(
                    onClick = onEditBikeProfile,
                    enabled = selectedBike != null,
                    modifier = Modifier.align(Alignment.CenterHorizontally),
                ) { Text(stringResource(R.string.home_edit_bike_profile)) }
            }
        }
    }
}

@Composable
private fun StartSessionCard(
    startingDetail: String,
    isStartingRepair: Boolean,
    onClose: () -> Unit,
    onStartingDetailChanged: (String) -> Unit,
    onStartRepair: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(22.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 5.dp),
    ) {
        Column(modifier = Modifier.padding(17.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        stringResource(R.string.home_start_session_title),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        stringResource(R.string.home_start_session_copy),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                TextButton(onClick = onClose) { Text(stringResource(R.string.home_close_setup)) }
            }
            Spacer(Modifier.height(14.dp))
            SessionTypeSelector()
            Spacer(Modifier.height(15.dp))
            DiagnoseStartingDetail(
                startingDetail = startingDetail,
                onStartingDetailChanged = onStartingDetailChanged,
            )
            Spacer(Modifier.height(13.dp))
            BeginDiagnosisButton(
                isStartingRepair = isStartingRepair,
                onStartRepair = onStartRepair,
            )
        }
    }
}

@Composable
private fun DiagnoseStartingDetail(
    startingDetail: String,
    onStartingDetailChanged: (String) -> Unit,
) {
    Text(
        stringResource(R.string.home_diagnose_prompt),
        style = MaterialTheme.typography.labelLarge,
        fontWeight = FontWeight.Bold,
    )
    Spacer(Modifier.height(7.dp))
    OutlinedTextField(
        value = startingDetail,
        onValueChange = onStartingDetailChanged,
        modifier = Modifier.fillMaxWidth(),
        minLines = 3,
        placeholder = { Text(stringResource(R.string.home_diagnose_placeholder)) },
        shape = RoundedCornerShape(13.dp),
        colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = MaterialTheme.colorScheme.secondary),
    )
    Spacer(Modifier.height(6.dp))
    Text(
        stringResource(R.string.home_diagnose_helper),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.labelSmall,
    )
}

@Composable
private fun BeginDiagnosisButton(
    isStartingRepair: Boolean,
    onStartRepair: () -> Unit,
) {
    Button(
        onClick = onStartRepair,
        enabled = !isStartingRepair,
        modifier = Modifier.fillMaxWidth().height(48.dp),
        shape = RoundedCornerShape(14.dp),
        colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.secondary),
    ) {
        Text(
            if (isStartingRepair) {
                stringResource(R.string.home_start_repair_in_progress)
            } else {
                stringResource(R.string.home_begin_diagnose_session)
            },
        )
    }
}

@Composable
private fun SessionTypeSelector() {
    Row(
        modifier =
            Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(16.dp))
                .padding(5.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        SessionTypeSegment(
            icon = "⌕",
            label = stringResource(R.string.home_session_type_diagnose),
            selected = true,
            enabled = true,
            modifier = Modifier.weight(1f),
        )
        SessionTypeSegment(
            icon = "✓",
            label = stringResource(R.string.home_session_type_checkup),
            selected = false,
            enabled = false,
            modifier = Modifier.weight(1f),
        )
        SessionTypeSegment(
            icon = "⌁",
            label = stringResource(R.string.home_session_type_repair),
            selected = false,
            enabled = false,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun SessionTypeSegment(
    icon: String,
    label: String,
    selected: Boolean,
    enabled: Boolean,
    modifier: Modifier,
) {
    Surface(
        modifier = modifier.height(62.dp),
        shape = RoundedCornerShape(12.dp),
        color = if (selected) MaterialTheme.colorScheme.surface else MaterialTheme.colorScheme.surfaceVariant,
        shadowElevation = if (selected) 2.dp else 0.dp,
    ) {
        Column(
            modifier = Modifier.padding(vertical = 7.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                icon,
                color =
                    if (enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                label,
                style = MaterialTheme.typography.labelSmall,
                color =
                    if (enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}
