package com.bikedoc.android.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R

@Composable
fun AuthScreen(viewModel: AuthViewModel) {
    val uiState by viewModel.uiState.collectAsState()
    AuthContent(
        state = uiState,
        onContinue = viewModel::continueToAuthenticatedShell,
    )
}

@Composable
private fun AuthContent(
    state: AuthUiState,
    onContinue: () -> Unit,
) {
    Scaffold { padding ->
        Column(
            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(horizontal = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = stringResource(R.string.auth_title),
                style = MaterialTheme.typography.headlineLarge,
            )
            Text(
                modifier = Modifier.padding(top = 12.dp),
                text = stringResource(R.string.auth_placeholder),
                style = MaterialTheme.typography.bodyMedium,
            )
            Button(
                modifier = Modifier.padding(top = 24.dp),
                enabled = !state.isLoading,
                onClick = onContinue,
            ) {
                Text(text = stringResource(R.string.auth_continue))
            }
        }
    }
}
