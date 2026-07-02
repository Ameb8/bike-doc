package com.bikedoc.android.bikes

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R

@Composable
fun BikeListScreen(
    viewModel: BikeListViewModel,
    onAddBike: () -> Unit,
    onOpenBike: (String) -> Unit,
) {
    val uiState by viewModel.uiState.collectAsState()
    BikeListContent(
        state = uiState,
        onRetry = viewModel::refresh,
        onAddBike = onAddBike,
        onBikeSelected = { bike ->
            if (!uiState.selectionMode) {
                onOpenBike(bike.id)
            }
        },
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BikeListContent(
    state: BikeListUiState,
    onRetry: () -> Unit,
    onAddBike: () -> Unit,
    onBikeSelected: (BikeListItem) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(text = stringResource(R.string.bike_list_title))
                        if (state.selectionMode) {
                            Text(
                                text = stringResource(R.string.bike_list_selection_subtitle),
                                style = MaterialTheme.typography.labelMedium,
                            )
                        }
                    }
                },
            )
        },
        floatingActionButton = {
            if (!state.selectionMode) {
                FloatingActionButton(onClick = onAddBike) {
                    Text(text = stringResource(R.string.bike_list_add_bike))
                }
            }
        },
    ) { padding ->
        BikeListBody(
            state = state,
            padding = padding,
            onRetry = onRetry,
            onAddBike = onAddBike,
            onBikeSelected = onBikeSelected,
        )
    }
}

@Composable
private fun BikeListBody(
    state: BikeListUiState,
    padding: PaddingValues,
    onRetry: () -> Unit,
    onAddBike: () -> Unit,
    onBikeSelected: (BikeListItem) -> Unit,
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
                actionLabel = if (state.selectionMode) null else stringResource(R.string.bike_list_add_bike),
                onAction = onAddBike,
            )

        else ->
            BikeListItems(
                bikes = state.bikes,
                padding = padding,
                onBikeSelected = onBikeSelected,
            )
    }
}

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
    padding: PaddingValues,
    onBikeSelected: (BikeListItem) -> Unit,
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
        }
    }
}
