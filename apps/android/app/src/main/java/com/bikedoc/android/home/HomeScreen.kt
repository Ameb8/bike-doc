package com.bikedoc.android.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R

@Composable
fun HomeScreen(viewModel: HomeViewModel) {
    val uiState by viewModel.uiState.collectAsState()
    HomeContent(
        state = uiState,
        onMyBikes = { viewModel.openBikes(selectionMode = false) },
        onStartRepair = { viewModel.openBikes(selectionMode = true) },
        onSignOut = viewModel::signOut,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HomeContent(
    state: HomeUiState,
    onMyBikes: () -> Unit,
    onStartRepair: () -> Unit,
    onSignOut: () -> Unit,
) {
    val menuExpanded = remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(text = stringResource(R.string.home_title)) },
                actions = {
                    Button(onClick = { menuExpanded.value = true }) {
                        Text(text = stringResource(R.string.home_menu))
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
            )
        },
    ) { padding ->
        Column(
            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            if (!state.isLoading && state.displayName != null) {
                Text(
                    text = stringResource(R.string.home_greeting, state.displayName),
                    style = MaterialTheme.typography.headlineSmall,
                )
            }
            state.error?.let {
                Text(
                    text = it,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            if (!state.isLoading && state.error == null) {
                Button(onClick = onMyBikes) {
                    Text(text = stringResource(R.string.home_my_bikes))
                }
                Button(onClick = onStartRepair) {
                    Text(text = stringResource(R.string.home_start_repair))
                }
            }
        }
    }
}
