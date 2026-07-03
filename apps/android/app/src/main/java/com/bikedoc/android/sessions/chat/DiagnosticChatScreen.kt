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
import com.bikedoc.android.sessions.models.DeliveryState
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
        onSubmitTextTurn = viewModel::submitTextTurn,
        onSubmitChoiceTurn = viewModel::submitChoiceTurn,
        onRetryMessage = viewModel::retryMessage,
        onNavigateBack = onNavigateBack,
    )
}

@Composable
private fun DiagnosticChatContent(
    state: DiagnosticChatUiState,
    onDraftTextChanged: (String) -> Unit,
    onSubmitTextTurn: () -> Unit,
    onSubmitChoiceTurn: (String) -> Unit,
    onRetryMessage: (String) -> Unit,
    onNavigateBack: () -> Unit,
) {
    Scaffold(
        topBar = { DiagnosticChatTopBar(onNavigateBack = onNavigateBack) },
        bottomBar = {
            DiagnosticInputArea(
                state = state,
                onDraftTextChanged = onDraftTextChanged,
                onSubmitTextTurn = onSubmitTextTurn,
                onSubmitChoiceTurn = onSubmitChoiceTurn,
            )
        },
    ) { padding ->
        DiagnosticChatBody(
            state = state,
            onRetryMessage = onRetryMessage,
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
    onRetryMessage: (String) -> Unit,
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

            else -> DiagnosticMessageList(state = state, onRetryMessage = onRetryMessage)
        }
    }
}

@Composable
private fun DiagnosticMessageList(
    state: DiagnosticChatUiState,
    onRetryMessage: (String) -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(state.messages, key = { it.id }) { message ->
            ChatMessageRow(
                message = message,
                onRetryMessage = onRetryMessage,
            )
        }

        if (state.isStreaming && state.streamingBubbleText.isNotBlank()) {
            item {
                ChatMessageRow(message = state.toStreamingMessage(), onRetryMessage = onRetryMessage)
            }
        }

        state.error?.let { error ->
            item {
                ChatErrorRow(error = error)
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
private fun ChatMessageRow(
    message: ChatMessage,
    onRetryMessage: (String) -> Unit,
) {
    Box(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier =
                Modifier
                    .align(message.containerAlignment())
                    .widthIn(max = 320.dp),
            horizontalAlignment = message.contentAlignment(),
        ) {
            Surface(
                color = message.containerColor(),
                shape = MaterialTheme.shapes.medium,
            ) {
                Text(
                    text = message.displayText(),
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                    style = message.textStyle(),
                    textAlign = message.textAlignment(),
                )
            }

            if (message.showRetry()) {
                RetryMessageRow(messageId = message.id, onRetryMessage = onRetryMessage)
            }
        }
    }
}

@Composable
private fun RetryMessageRow(
    messageId: String,
    onRetryMessage: (String) -> Unit,
) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = stringResource(R.string.diagnostic_chat_failed),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
        )
        TextButton(onClick = { onRetryMessage(messageId) }) {
            Text(text = stringResource(R.string.diagnostic_chat_retry))
        }
    }
}

@Composable
private fun ChatErrorRow(error: String) {
    Box(modifier = Modifier.fillMaxWidth()) {
        Text(
            text = error,
            modifier =
                Modifier
                    .align(Alignment.Center)
                    .padding(horizontal = 24.dp, vertical = 8.dp),
            color = MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodySmall,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun DiagnosticInputArea(
    state: DiagnosticChatUiState,
    onDraftTextChanged: (String) -> Unit,
    onSubmitTextTurn: () -> Unit,
    onSubmitChoiceTurn: (String) -> Unit,
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
                onSubmitTextTurn = onSubmitTextTurn,
                onSubmitChoiceTurn = onSubmitChoiceTurn,
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
    onSubmitTextTurn: () -> Unit,
    onSubmitChoiceTurn: (String) -> Unit,
) {
    val isInputDisabled = state.isTurnInFlight || state.isStreaming
    Surface(
        tonalElevation = 3.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            InputPrompt(inputRequest = inputRequest)
            when (inputRequest?.type) {
                "decision" -> {
                    ChoiceRow(
                        inputRequest = inputRequest,
                        enabled = !isInputDisabled,
                        onChoiceSelected = onSubmitChoiceTurn,
                    )
                }

                "confirmation" -> {
                    ConfirmationRow(
                        inputRequest = inputRequest,
                        enabled = !isInputDisabled,
                        onConfirm = onSubmitChoiceTurn,
                    )
                }

                "multiple_choice" -> {
                    ChoiceRow(
                        inputRequest = inputRequest,
                        enabled = !isInputDisabled,
                        onChoiceSelected = onSubmitChoiceTurn,
                    )
                    ReplyRow(
                        state = state,
                        onDraftTextChanged = onDraftTextChanged,
                        onSubmitTextTurn = onSubmitTextTurn,
                    )
                }

                else -> {
                    ReplyRow(
                        state = state,
                        onDraftTextChanged = onDraftTextChanged,
                        onSubmitTextTurn = onSubmitTextTurn,
                    )
                }
            }
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
    onSubmitTextTurn: () -> Unit,
) {
    val isEnabled = !state.isTurnInFlight && !state.isStreaming
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            value = state.draftText,
            onValueChange = onDraftTextChanged,
            modifier = Modifier.weight(1f),
            enabled = isEnabled,
            label = { Text(text = stringResource(R.string.diagnostic_chat_reply_label)) },
            singleLine = false,
            minLines = 1,
            maxLines = 4,
        )
        Button(
            onClick = onSubmitTextTurn,
            enabled = isEnabled && state.draftText.isNotBlank(),
        ) {
            Text(text = stringResource(R.string.diagnostic_chat_send))
        }
    }
}

@Composable
private fun ChoiceRow(
    inputRequest: InputRequest?,
    enabled: Boolean,
    onChoiceSelected: (String) -> Unit,
) {
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
                onClick = { onChoiceSelected(CONFIRMATION_VALUE) },
                enabled = enabled,
                label = { Text(text = stringResource(R.string.diagnostic_chat_confirm)) },
            )
        } else {
            request.choices.forEach { choice ->
                AssistChip(
                    onClick = { onChoiceSelected(choice.id) },
                    enabled = enabled,
                    label = { Text(text = choice.label) },
                )
            }
        }
    }
}

@Composable
private fun ConfirmationRow(
    inputRequest: InputRequest?,
    enabled: Boolean,
    onConfirm: (String) -> Unit,
) {
    val confirmationValue = inputRequest?.choices?.firstOrNull()?.id ?: CONFIRMATION_VALUE
    Button(
        onClick = { onConfirm(confirmationValue) },
        enabled = enabled,
    ) {
        Text(text = stringResource(R.string.diagnostic_chat_confirm))
    }
}

private const val CONFIRMATION_VALUE = "confirm"

private fun ChatMessage.containerAlignment() =
    when (role) {
        Role.User -> Alignment.CenterEnd
        Role.Assistant -> Alignment.CenterStart
        Role.System -> Alignment.Center
    }

private fun ChatMessage.contentAlignment() =
    when (role) {
        Role.User -> Alignment.End
        Role.Assistant -> Alignment.Start
        Role.System -> Alignment.CenterHorizontally
    }

@Composable
private fun ChatMessage.containerColor() =
    when (role) {
        Role.User -> MaterialTheme.colorScheme.primaryContainer
        Role.Assistant -> MaterialTheme.colorScheme.surfaceVariant
        Role.System -> MaterialTheme.colorScheme.surface
    }

private fun ChatMessage.displayText() = if (isStreaming) "$text ..." else text

@Composable
private fun ChatMessage.textStyle() =
    if (role == Role.System) {
        MaterialTheme.typography.bodyMedium
    } else {
        MaterialTheme.typography.bodyLarge
    }

private fun ChatMessage.textAlignment() =
    if (role == Role.System) {
        TextAlign.Center
    } else {
        TextAlign.Start
    }

private fun ChatMessage.showRetry() = role == Role.User && deliveryState == DeliveryState.Failed
