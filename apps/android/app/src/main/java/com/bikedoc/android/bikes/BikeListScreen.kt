package com.bikedoc.android.bikes

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R
import com.bikedoc.android.navigation.UiEvent
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

@Composable
fun BikeListScreen(
    viewModel: BikeListViewModel,
    onAddBike: () -> Unit,
    onOpenBike: (String) -> Unit,
    onNavigateTo: (String) -> Unit,
) {
    val uiState by viewModel.uiState.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                is UiEvent.ShowSnackbar -> snackbarHostState.showSnackbar(event.message)
                is UiEvent.NavigateTo -> onNavigateTo(event.route)
                else -> Unit
            }
        }
    }

    BikeListContent(
        state = uiState,
        snackbarHostState = snackbarHostState,
        onRetry = viewModel::refresh,
        onAddBike = onAddBike,
        onBikeSelected = { bike ->
            if (uiState.selectionMode) {
                viewModel.selectBike(bike)
            } else {
                onOpenBike(bike.id)
            }
        },
        onRequestDelete = viewModel::requestDelete,
        onDismissDelete = viewModel::dismissDelete,
        onConfirmDelete = viewModel::confirmDelete,
        onDismissSessionChooser = viewModel::dismissSessionChooser,
        onResumeLatestSession = viewModel::resumeLatestSession,
        onSelectSessionFromChooser = viewModel::selectSessionFromChooser,
        onRequestStartNewSession = viewModel::requestStartNewSessionFromChooser,
        onDismissStartNewSessionConfirmation = viewModel::dismissStartNewSessionConfirmation,
        onConfirmStartNewSession = viewModel::confirmStartNewSessionFromChooser,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BikeListContent(
    state: BikeListUiState,
    snackbarHostState: SnackbarHostState,
    onRetry: () -> Unit,
    onAddBike: () -> Unit,
    onBikeSelected: (BikeListItem) -> Unit,
    onRequestDelete: (BikeListItem) -> Unit,
    onDismissDelete: () -> Unit,
    onConfirmDelete: () -> Unit,
    onDismissSessionChooser: () -> Unit,
    onResumeLatestSession: () -> Unit,
    onSelectSessionFromChooser: (String) -> Unit,
    onRequestStartNewSession: () -> Unit,
    onDismissStartNewSessionConfirmation: () -> Unit,
    onConfirmStartNewSession: () -> Unit,
) {
    Scaffold(
        snackbarHost = { SnackbarHost(hostState = snackbarHostState) },
        topBar = { BikeListTopBar(selectionMode = state.selectionMode) },
        floatingActionButton = {
            BikeListFloatingActionButton(
                selectionMode = state.selectionMode,
                onAddBike = onAddBike,
            )
        },
    ) { padding ->
        BikeListBody(
            state = state,
            padding = padding,
            onRetry = onRetry,
            onAddBike = onAddBike,
            onBikeSelected = onBikeSelected,
            onRequestDelete = onRequestDelete,
        )
        BikeListOverlays(
            state = state,
            onDismissDelete = onDismissDelete,
            onConfirmDelete = onConfirmDelete,
            onDismissSessionChooser = onDismissSessionChooser,
            onResumeLatestSession = onResumeLatestSession,
            onSelectSessionFromChooser = onSelectSessionFromChooser,
            onRequestStartNewSession = onRequestStartNewSession,
            onDismissStartNewSessionConfirmation = onDismissStartNewSessionConfirmation,
            onConfirmStartNewSession = onConfirmStartNewSession,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BikeListTopBar(selectionMode: Boolean) {
    TopAppBar(
        title = {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(text = stringResource(R.string.bike_list_title))
                if (selectionMode) {
                    Text(
                        text = stringResource(R.string.bike_list_selection_subtitle),
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
            }
        },
    )
}

@Composable
private fun BikeListFloatingActionButton(
    selectionMode: Boolean,
    onAddBike: () -> Unit,
) {
    if (!selectionMode) {
        FloatingActionButton(onClick = onAddBike) {
            Text(text = stringResource(R.string.bike_list_add_bike))
        }
    }
}

@Composable
private fun BikeListOverlays(
    state: BikeListUiState,
    onDismissDelete: () -> Unit,
    onConfirmDelete: () -> Unit,
    onDismissSessionChooser: () -> Unit,
    onResumeLatestSession: () -> Unit,
    onSelectSessionFromChooser: (String) -> Unit,
    onRequestStartNewSession: () -> Unit,
    onDismissStartNewSessionConfirmation: () -> Unit,
    onConfirmStartNewSession: () -> Unit,
) {
    state.pendingDeleteBike?.let { bike ->
        DeleteBikeDialog(
            bikeName = bike.name,
            onDismiss = onDismissDelete,
            onConfirm = onConfirmDelete,
        )
    }
    state.sessionChooser?.let { chooser ->
        SessionChooserSheet(
            chooser = chooser,
            isCreatingSession = state.isCreatingSession,
            onDismiss = onDismissSessionChooser,
            onResumeLatestSession = onResumeLatestSession,
            onSelectSession = onSelectSessionFromChooser,
            onStartNewSession = onRequestStartNewSession,
        )
    }
    if (state.showStartNewSessionConfirmation) {
        StartNewSessionConfirmationDialog(
            isCreatingSession = state.isCreatingSession,
            onDismiss = onDismissStartNewSessionConfirmation,
            onConfirm = onConfirmStartNewSession,
        )
    }
}

@Composable
private fun DeleteBikeDialog(
    bikeName: String,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(text = stringResource(R.string.bike_list_delete_confirm_title, bikeName))
        },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(text = stringResource(R.string.bike_list_delete_confirm_action))
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(text = stringResource(R.string.bike_list_delete_cancel_action))
            }
        },
    )
}

@Composable
private fun StartNewSessionConfirmationDialog(
    isCreatingSession: Boolean,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(text = stringResource(R.string.session_chooser_start_new_confirmation_title))
        },
        text = {
            Text(text = stringResource(R.string.session_chooser_start_new_confirmation_message))
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                enabled = !isCreatingSession,
            ) {
                Text(text = stringResource(R.string.session_chooser_start_new_action))
            }
        },
        dismissButton = {
            TextButton(
                onClick = onDismiss,
                enabled = !isCreatingSession,
            ) {
                Text(text = stringResource(R.string.bike_list_delete_cancel_action))
            }
        },
    )
}

@Composable
private fun BikeListBody(
    state: BikeListUiState,
    padding: PaddingValues,
    onRetry: () -> Unit,
    onAddBike: () -> Unit,
    onBikeSelected: (BikeListItem) -> Unit,
    onRequestDelete: (BikeListItem) -> Unit,
) {
    when {
        state.isLoading -> BikeListLoadingState(padding = padding)
        state.error != null ->
            BikeListMessageState(
                padding = padding,
                title = stringResource(R.string.bike_list_error_title),
                message = state.error,
                actionLabel = stringResource(R.string.bike_list_retry),
                onAction = onRetry,
            )

        state.bikes.isEmpty() ->
            BikeListMessageState(
                padding = padding,
                title = stringResource(R.string.bike_list_empty_title),
                message = stringResource(R.string.bike_list_empty_message),
                actionLabel =
                    if (state.selectionMode) {
                        null
                    } else {
                        stringResource(R.string.bike_list_add_bike)
                    },
                onAction = onAddBike,
            )

        else ->
            BikeListItems(
                bikes = state.bikes,
                selectionMode = state.selectionMode,
                deletingBikeId = state.deletingBikeId,
                selectedBikeId = state.selectedBikeId,
                isBusy = state.isLoadingBikeSessions || state.isCreatingSession,
                padding = padding,
                onBikeSelected = onBikeSelected,
                onRequestDelete = onRequestDelete,
            )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SessionChooserSheet(
    chooser: SessionChooserState,
    isCreatingSession: Boolean,
    onDismiss: () -> Unit,
    onResumeLatestSession: () -> Unit,
    onSelectSession: (String) -> Unit,
    onStartNewSession: () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        SessionChooserSheetContent(
            chooser = chooser,
            isCreatingSession = isCreatingSession,
            onResumeLatestSession = onResumeLatestSession,
            onSelectSession = onSelectSession,
            onStartNewSession = onStartNewSession,
        )
    }
}

@Composable
private fun SessionChooserSheetContent(
    chooser: SessionChooserState,
    isCreatingSession: Boolean,
    onResumeLatestSession: () -> Unit,
    onSelectSession: (String) -> Unit,
    onStartNewSession: () -> Unit,
) {
    Column(
        modifier =
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = stringResource(R.string.session_chooser_title),
            style = MaterialTheme.typography.titleLarge,
        )
        chooser.primaryResumeSession?.let { session ->
            PrimaryResumeSessionButton(
                session = session,
                isCreatingSession = isCreatingSession,
                onResumeLatestSession = onResumeLatestSession,
            )
        }
        StartNewSessionButton(
            isCreatingSession = isCreatingSession,
            onStartNewSession = onStartNewSession,
        )
        OlderSessionList(
            sessions = chooser.olderSessions,
            isCreatingSession = isCreatingSession,
            onSelectSession = onSelectSession,
        )
        Spacer(modifier = Modifier.height(8.dp))
    }
}

@Composable
private fun PrimaryResumeSessionButton(
    session: SessionChooserItem,
    isCreatingSession: Boolean,
    onResumeLatestSession: () -> Unit,
) {
    Button(
        onClick = onResumeLatestSession,
        enabled = !isCreatingSession,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(text = stringResource(R.string.session_chooser_resume_latest_action))
            Text(
                text = formatSessionTimestamp(session.createdAt),
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                text = session.statusLabel,
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                text = session.description,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun StartNewSessionButton(
    isCreatingSession: Boolean,
    onStartNewSession: () -> Unit,
) {
    Button(
        onClick = onStartNewSession,
        enabled = !isCreatingSession,
        modifier = Modifier.fillMaxWidth(),
        colors = ButtonDefaults.buttonColors(),
    ) {
        Text(
            text =
                if (isCreatingSession) {
                    stringResource(R.string.session_chooser_start_new_in_progress)
                } else {
                    stringResource(R.string.session_chooser_start_new_action)
                },
        )
    }
}

@Composable
private fun OlderSessionList(
    sessions: List<SessionChooserItem>,
    isCreatingSession: Boolean,
    onSelectSession: (String) -> Unit,
) {
    if (sessions.isEmpty()) {
        return
    }

    Text(
        text = stringResource(R.string.session_chooser_previous_sessions_title),
        style = MaterialTheme.typography.titleMedium,
    )
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        sessions.forEach { session ->
            SessionChooserRow(
                session = session,
                enabled = session.isResumable && !isCreatingSession,
                onClick = { onSelectSession(session.id) },
            )
        }
    }
}

@Composable
private fun SessionChooserRow(
    session: SessionChooserItem,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Card(
        modifier =
            Modifier
                .fillMaxWidth()
                .then(
                    if (enabled) {
                        Modifier.clickable(onClick = onClick)
                    } else {
                        Modifier
                    },
                ),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = formatSessionTimestamp(session.createdAt),
                    style = MaterialTheme.typography.titleSmall,
                )
                if (session.isResumable) {
                    AssistChip(
                        onClick = onClick,
                        enabled = enabled,
                        label = { Text(text = stringResource(R.string.session_chooser_resumable_badge)) },
                    )
                }
            }
            Text(
                text = session.statusLabel,
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                text = session.description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun formatSessionTimestamp(createdAt: String): String =
    runCatching {
        OffsetDateTime
            .parse(createdAt)
            .format(DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM))
    }.getOrDefault(createdAt)

@Composable
private fun BikeListLoadingState(padding: PaddingValues) {
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
private fun BikeListItems(
    bikes: List<BikeListItem>,
    selectionMode: Boolean,
    deletingBikeId: String?,
    selectedBikeId: String?,
    isBusy: Boolean,
    padding: PaddingValues,
    onBikeSelected: (BikeListItem) -> Unit,
    onRequestDelete: (BikeListItem) -> Unit,
) {
    LazyColumn(
        modifier =
            Modifier
                .fillMaxSize()
                .padding(padding),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(
            items = bikes,
            key = { it.id },
        ) { bike ->
            BikeRow(
                bike = bike,
                onClick = { onBikeSelected(bike) },
                showDeleteAction = !selectionMode && !bike.hasRepairSessions,
                isDeleting = deletingBikeId == bike.id,
                showSelectionProgress = selectionMode && isBusy && selectedBikeId == bike.id,
                onDelete = { onRequestDelete(bike) },
            )
        }
    }
}

@Composable
private fun BikeListMessageState(
    padding: PaddingValues,
    title: String,
    message: String,
    actionLabel: String?,
    onAction: () -> Unit,
) {
    Box(
        modifier =
            Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(text = title, style = MaterialTheme.typography.headlineSmall)
            Text(text = message, style = MaterialTheme.typography.bodyLarge)
            if (actionLabel != null) {
                Button(onClick = onAction) {
                    Text(text = actionLabel)
                }
            }
        }
    }
}

@Composable
private fun BikeRow(
    bike: BikeListItem,
    onClick: () -> Unit,
    showDeleteAction: Boolean,
    isDeleting: Boolean,
    showSelectionProgress: Boolean,
    onDelete: () -> Unit,
) {
    Card(
        modifier =
            Modifier
                .fillMaxWidth()
                .clickable(onClick = onClick),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = bike.name,
                style = MaterialTheme.typography.titleMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = bike.makeModelYear,
                style = MaterialTheme.typography.bodyMedium,
            )
            Row {
                Text(
                    text = bike.specificationSummary,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (showSelectionProgress) {
                CircularProgressIndicator()
            }
            if (showDeleteAction) {
                TextButton(
                    onClick = onDelete,
                    enabled = !isDeleting,
                ) {
                    Text(
                        text =
                            if (isDeleting) {
                                stringResource(R.string.bike_list_delete_in_progress)
                            } else {
                                stringResource(R.string.bike_list_delete_action)
                            },
                    )
                }
            }
        }
    }
}
