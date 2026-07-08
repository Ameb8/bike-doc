package com.bikedoc.android.auth

import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R

@Composable
fun AuthScreen(viewModel: AuthViewModel) {
    val uiState by viewModel.uiState.collectAsState()
    AuthContent(
        state = uiState,
        onModeSelected = viewModel::onModeSelected,
        onEmailChanged = viewModel::onEmailChanged,
        onPasswordChanged = viewModel::onPasswordChanged,
        onConfirmPasswordChanged = viewModel::onConfirmPasswordChanged,
        onSubmit = viewModel::submit,
    )
}

@Composable
private fun AuthContent(
    state: AuthUiState,
    onModeSelected: (AuthMode) -> Unit,
    onEmailChanged: (String) -> Unit,
    onPasswordChanged: (String) -> Unit,
    onConfirmPasswordChanged: (String) -> Unit,
    onSubmit: () -> Unit,
) {
    val isPasswordVisible = remember { mutableStateOf(false) }
    val isConfirmPasswordVisible = remember { mutableStateOf(false) }

    Scaffold { padding ->
        Column(
            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(horizontal = 24.dp, vertical = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                text = stringResource(R.string.auth_title),
                style = MaterialTheme.typography.headlineLarge,
            )
            AuthModeTabs(
                selectedMode = state.mode,
                onModeSelected = onModeSelected,
            )
            AuthFormFields(
                state = state,
                isPasswordVisible = isPasswordVisible.value,
                isConfirmPasswordVisible = isConfirmPasswordVisible.value,
                onEmailChanged = onEmailChanged,
                onPasswordChanged = onPasswordChanged,
                onConfirmPasswordChanged = onConfirmPasswordChanged,
                onTogglePasswordVisibility = { isPasswordVisible.value = !isPasswordVisible.value },
                onToggleConfirmPasswordVisibility = {
                    isConfirmPasswordVisible.value = !isConfirmPasswordVisible.value
                },
                onSubmit = onSubmit,
            )
        }
    }
}

@Composable
private fun AuthFormFields(
    state: AuthUiState,
    isPasswordVisible: Boolean,
    isConfirmPasswordVisible: Boolean,
    onEmailChanged: (String) -> Unit,
    onPasswordChanged: (String) -> Unit,
    onConfirmPasswordChanged: (String) -> Unit,
    onTogglePasswordVisibility: () -> Unit,
    onToggleConfirmPasswordVisibility: () -> Unit,
    onSubmit: () -> Unit,
) {
    AuthEmailField(
        email = state.email,
        error = state.validationMessages[AuthField.Email],
        onEmailChanged = onEmailChanged,
    )
    AuthPasswordField(
        value = state.password,
        label = stringResource(R.string.auth_password_label),
        isVisible = isPasswordVisible,
        error = state.validationMessages[AuthField.Password],
        imeAction = if (state.mode == AuthMode.CreateAccount) ImeAction.Next else ImeAction.Done,
        onToggleVisibility = onTogglePasswordVisibility,
        onValueChange = onPasswordChanged,
    )
    if (state.mode == AuthMode.CreateAccount) {
        AuthPasswordField(
            value = state.confirmPassword,
            label = stringResource(R.string.auth_confirm_password_label),
            isVisible = isConfirmPasswordVisible,
            error = state.validationMessages[AuthField.ConfirmPassword],
            imeAction = ImeAction.Done,
            onToggleVisibility = onToggleConfirmPasswordVisibility,
            onValueChange = onConfirmPasswordChanged,
        )
    }
    Button(
        modifier = Modifier.padding(top = 8.dp),
        enabled = state.activeOperation == null,
        onClick = onSubmit,
    ) {
        Text(
            text =
                stringResource(
                    if (state.mode == AuthMode.SignIn) {
                        R.string.auth_tab_sign_in
                    } else {
                        R.string.auth_tab_create_account
                    },
                ),
        )
    }
    state.message?.let {
        Text(
            text = stringResource(it.stringRes()),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.error,
        )
    }
}

@Composable
private fun AuthModeTabs(
    selectedMode: AuthMode,
    onModeSelected: (AuthMode) -> Unit,
) {
    TabRow(selectedTabIndex = selectedMode.ordinal) {
        Tab(
            selected = selectedMode == AuthMode.SignIn,
            onClick = { onModeSelected(AuthMode.SignIn) },
            text = { Text(text = stringResource(R.string.auth_tab_sign_in)) },
        )
        Tab(
            selected = selectedMode == AuthMode.CreateAccount,
            onClick = { onModeSelected(AuthMode.CreateAccount) },
            text = { Text(text = stringResource(R.string.auth_tab_create_account)) },
        )
    }
}

@Composable
private fun AuthEmailField(
    email: String,
    error: AuthMessage?,
    onEmailChanged: (String) -> Unit,
) {
    OutlinedTextField(
        value = email,
        onValueChange = onEmailChanged,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(text = stringResource(R.string.auth_email_label)) },
        singleLine = true,
        keyboardOptions =
            KeyboardOptions(
                keyboardType = KeyboardType.Email,
                imeAction = ImeAction.Next,
            ),
        isError = error != null,
        supportingText = {
            error?.let { Text(text = stringResource(it.stringRes())) }
        },
    )
}

@Composable
private fun AuthPasswordField(
    value: String,
    label: String,
    isVisible: Boolean,
    error: AuthMessage?,
    imeAction: ImeAction,
    onToggleVisibility: () -> Unit,
    onValueChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(text = label) },
        singleLine = true,
        visualTransformation =
            if (isVisible) {
                VisualTransformation.None
            } else {
                PasswordVisualTransformation()
            },
        trailingIcon = {
            TextButton(onClick = onToggleVisibility) {
                Text(
                    text =
                        stringResource(
                            if (isVisible) {
                                R.string.auth_hide_password
                            } else {
                                R.string.auth_show_password
                            },
                        ),
                )
            }
        },
        keyboardOptions =
            KeyboardOptions(
                keyboardType = KeyboardType.Password,
                imeAction = imeAction,
            ),
        isError = error != null,
        supportingText = {
            error?.let { Text(text = stringResource(it.stringRes())) }
        },
    )
}

@StringRes
private fun AuthMessage.stringRes(): Int =
    when (this) {
        AuthMessage.EmailRequired -> R.string.auth_error_email_required
        AuthMessage.PasswordTooShort -> R.string.auth_error_password_too_short
        AuthMessage.PasswordsDoNotMatch -> R.string.auth_error_passwords_do_not_match
        AuthMessage.InvalidEmail -> R.string.auth_error_invalid_email
        AuthMessage.InvalidCredentials -> R.string.auth_error_invalid_credentials
        AuthMessage.SignInFailed -> R.string.auth_error_sign_in_failed
        AuthMessage.EmailAlreadyInUse -> R.string.auth_error_email_already_in_use
        AuthMessage.CreateAccountFailed -> R.string.auth_error_create_account_failed
    }
