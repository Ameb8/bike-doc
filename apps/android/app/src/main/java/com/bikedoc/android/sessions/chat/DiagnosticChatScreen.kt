package com.bikedoc.android.sessions.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R
import com.bikedoc.android.api.models.InputRequest
import com.bikedoc.android.sessions.models.ChatMessage
import com.bikedoc.android.sessions.models.Role

@Composable
fun DiagnosticChatScreen(
    viewModel: DiagnosticChatViewModel,
    onNavigateBack: () -> Unit,
) {
    val state by viewModel.uiState.collectAsState()

    DiagnosticChatContent(
        state = state,
        onDraftTextChanged = viewModel::onDraftTextChanged,
        onNavigateBack = onNavigateBack,
    )
}

@Composable
private fun DiagnosticChatContent(
    state: DiagnosticChatUiState,
    onDraftTextChanged: (String) -> Unit,
    onNavigateBack: () -> Unit,
) {
    Scaffold(
        topBar = { DiagnosticChatTopBar(onNavigateBack = onNavigateBack) },
        bottomBar = {
            DiagnosticInputArea(
                state = state,
                onDraftTextChanged = onDraftTextChanged,
            )
        },
    ) { padding ->
        DiagnosticChatBody(
            state = state,
            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(padding),
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DiagnosticChatTopBar(onNavigateBack: () -> Unit) {
    TopAppBar(
        title = {
            Column {
                Text(text = stringResource(R.string.diagnostic_chat_title))
                Text(
                    text = stringResource(R.string.diagnostic_chat_subtitle),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        },
        navigationIcon = {
            TextButton(onClick = onNavigateBack) {
                Text(text = stringResource(R.string.bike_edit_back))
            }
        },
    )
}

@Composable
private fun DiagnosticChatBody(
    state: DiagnosticChatUiState,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier) {
        when {
            state.isLoadingSession ->
                CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))

            state.error != null && state.messages.isEmpty() ->
                Text(
                    text = state.error,
                    modifier =
                        Modifier
                            .align(Alignment.Center)
                            .padding(24.dp),
                    textAlign = TextAlign.Center,
                )

            else -> DiagnosticMessageList(state = state)
        }
    }
}

@Composable
private fun DiagnosticMessageList(state: DiagnosticChatUiState) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(state.messages, key = { it.id }) { message ->
            ChatMessageRow(message = message)
        }

        if (state.isStreaming && state.streamingBubbleText.isNotBlank()) {
            item {
                ChatMessageRow(message = state.toStreamingMessage())
            }
        }
    }
}

private fun DiagnosticChatUiState.toStreamingMessage() =
    ChatMessage(
        id = "streaming",
        role = Role.Assistant,
        text = streamingBubbleText,
        isStreaming = true,
        createdAt = java.time.Instant.now(),
    )

@Composable
private fun ChatMessageRow(message: ChatMessage) {
    val alignment =
        when (message.role) {
            Role.User -> Alignment.CenterEnd
            Role.Assistant -> Alignment.CenterStart
            Role.System -> Alignment.Center
        }
    val color =
        when (message.role) {
            Role.User -> MaterialTheme.colorScheme.primaryContainer
            Role.Assistant -> MaterialTheme.colorScheme.surfaceVariant
            Role.System -> MaterialTheme.colorScheme.surface
        }

    Box(modifier = Modifier.fillMaxWidth()) {
        Surface(
            modifier =
                Modifier
                    .align(alignment)
                    .widthIn(max = 320.dp),
            color = color,
            shape = MaterialTheme.shapes.medium,
        ) {
            Text(
                text = if (message.isStreaming) message.text + " ..." else message.text,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                style =
                    if (message.role == Role.System) {
                        MaterialTheme.typography.bodyMedium
                    } else {
                        MaterialTheme.typography.bodyLarge
                    },
                textAlign = if (message.role == Role.System) TextAlign.Center else TextAlign.Start,
            )
        }
    }
}

@Composable
private fun DiagnosticInputArea(
    state: DiagnosticChatUiState,
    onDraftTextChanged: (String) -> Unit,
) {
    val inputRequest = state.inputRequest
    when {
        state.isLoadingSession || state.session == null -> Unit
        state.phaseTransitioned -> DiagnosticCompleteBanner()
        inputRequest?.type == "none" -> Unit
        else ->
            ActiveInputArea(
                state = state,
                inputRequest = inputRequest,
                onDraftTextChanged = onDraftTextChanged,
            )
    }
}

@Composable
private fun DiagnosticCompleteBanner() {
    Surface(
        tonalElevation = 3.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            text = stringResource(R.string.diagnostic_chat_complete_banner),
            modifier = Modifier.padding(16.dp),
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.titleMedium,
        )
    }
}

@Composable
private fun ActiveInputArea(
    state: DiagnosticChatUiState,
    inputRequest: InputRequest?,
    onDraftTextChanged: (String) -> Unit,
) {
    Surface(
        tonalElevation = 3.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            InputPrompt(inputRequest = inputRequest)
            ChoiceRow(inputRequest = inputRequest)
            ReplyRow(
                state = state,
                onDraftTextChanged = onDraftTextChanged,
            )
        }
    }
}

@Composable
private fun InputPrompt(inputRequest: InputRequest?) {
    inputRequest?.prompt?.takeIf { it.isNotBlank() }?.let { prompt ->
        Text(
            text = prompt,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun ReplyRow(
    state: DiagnosticChatUiState,
    onDraftTextChanged: (String) -> Unit,
) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            value = state.draftText,
            onValueChange = onDraftTextChanged,
            modifier = Modifier.weight(1f),
            enabled = !state.isTurnInFlight && !state.isStreaming,
            label = { Text(text = stringResource(R.string.diagnostic_chat_reply_label)) },
            singleLine = false,
            minLines = 1,
            maxLines = 4,
        )
        Button(
            onClick = {},
            enabled = false,
        ) {
            Text(text = stringResource(R.string.diagnostic_chat_send))
        }
    }
}

@Composable
private fun ChoiceRow(inputRequest: InputRequest?) {
    val request = inputRequest ?: return
    if (request.type !in setOf("multiple_choice", "decision", "confirmation")) {
        return
    }

    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        if (request.type == "confirmation" && request.choices.isEmpty()) {
            AssistChip(
                onClick = {},
                label = { Text(text = stringResource(R.string.diagnostic_chat_confirm)) },
            )
        } else {
            request.choices.forEach { choice ->
                AssistChip(
                    onClick = {},
                    label = { Text(text = choice.label) },
                )
            }
        }
    }
}
