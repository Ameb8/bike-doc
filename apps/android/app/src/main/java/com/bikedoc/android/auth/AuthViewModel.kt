package com.bikedoc.android.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bikedoc.android.navigation.AppRoute
import com.bikedoc.android.navigation.UiEvent
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class AuthUiState(
    val mode: AuthMode = AuthMode.SignIn,
    val email: String = "",
    val password: String = "",
    val confirmPassword: String = "",
    val activeOperation: AuthOperation? = null,
    val isLinkingGoogle: Boolean = false,
    val linkingGoogleEmail: String? = null,
    val message: AuthMessage? = null,
    val validationMessages: Map<AuthField, AuthMessage> = emptyMap(),
)

enum class AuthOperation {
    EmailPassword,
    Google,
}

enum class AuthField {
    Email,
    Password,
    ConfirmPassword,
}

enum class AuthMessage {
    EmailRequired,
    PasswordTooShort,
    PasswordsDoNotMatch,
    InvalidEmail,
    InvalidCredentials,
    SignInFailed,
    EmailAlreadyInUse,
    CreateAccountFailed,
    NoGoogleCredential,
    GoogleProviderUnavailable,
    MissingGoogleIdToken,
    GoogleSignInFailed,
    GoogleLinkRequired,
}

@HiltViewModel
class AuthViewModel
    @Inject
    constructor(
        private val authProvider: AuthProvider,
    ) : ViewModel() {
        private val _uiState = MutableStateFlow(AuthUiState())
        val uiState: StateFlow<AuthUiState> = _uiState.asStateFlow()

        private val eventChannel = Channel<UiEvent>(Channel.BUFFERED)
        val events = eventChannel.receiveAsFlow()

        private var pendingGoogleCredential: PendingAuthCredential? = null

        fun onModeSelected(mode: AuthMode) {
            if (mode == AuthMode.CreateAccount) {
                clearPendingGoogleLink()
            }
            _uiState.value =
                _uiState.value.copy(
                    mode = mode,
                    isLinkingGoogle = isGoogleLinkingActive(),
                    linkingGoogleEmail = pendingGoogleCredential?.let { _uiState.value.linkingGoogleEmail },
                    message = null,
                    validationMessages = emptyMap(),
                )
        }

        fun onEmailChanged(email: String) {
            _uiState.value =
                _uiState.value.copy(
                    email = email,
                    message = null,
                    validationMessages = _uiState.value.validationMessages - AuthField.Email,
                )
        }

        fun onPasswordChanged(password: String) {
            _uiState.value =
                _uiState.value.copy(
                    password = password,
                    message = null,
                    validationMessages = _uiState.value.validationMessages - AuthField.Password,
                )
        }

        fun onConfirmPasswordChanged(confirmPassword: String) {
            _uiState.value =
                _uiState.value.copy(
                    confirmPassword = confirmPassword,
                    message = null,
                    validationMessages =
                        _uiState.value.validationMessages - AuthField.ConfirmPassword,
                )
        }

        fun submit() {
            viewModelScope.launch {
                val currentState = _uiState.value
                if (currentState.activeOperation != null) {
                    return@launch
                }
                val validationErrors =
                    if (currentState.mode == AuthMode.CreateAccount) {
                        validateCreateAccount(currentState)
                    } else {
                        emptyMap()
                    }

                if (validationErrors.isNotEmpty()) {
                    _uiState.value =
                        currentState.copy(
                            activeOperation = null,
                            message = null,
                            validationMessages = validationErrors,
                        )
                    return@launch
                }

                _uiState.value =
                    currentState.copy(
                        activeOperation = AuthOperation.EmailPassword,
                        message = null,
                        validationMessages = emptyMap(),
                    )

                when (val result = submitCredentials()) {
                    AuthResult.Success -> {
                        _uiState.value = _uiState.value.copy(activeOperation = null)
                        eventChannel.send(UiEvent.NavigateTo(AppRoute.Home.route))
                    }
                    AuthResult.Cancelled -> {
                        _uiState.value = _uiState.value.copy(activeOperation = null)
                    }
                    is AuthResult.Failure -> {
                        _uiState.value =
                            _uiState.value.copy(
                                activeOperation = null,
                                message = mapError(_uiState.value.mode, result.reason),
                            )
                    }
                    is AuthResult.LinkRequired -> {
                        enterGoogleLinkingMode(result)
                    }
                }
            }
        }

        fun continueWithGoogle(host: GoogleSignInHost) {
            viewModelScope.launch {
                val currentState = _uiState.value
                if (currentState.activeOperation != null) {
                    return@launch
                }

                _uiState.value =
                    currentState.copy(
                        activeOperation = AuthOperation.Google,
                        isLinkingGoogle = false,
                        linkingGoogleEmail = null,
                        message = null,
                    )
                clearPendingGoogleCredentialOnly()

                when (val result = authProvider.continueWithGoogle(host)) {
                    AuthResult.Success -> {
                        _uiState.value = _uiState.value.copy(activeOperation = null)
                        eventChannel.send(UiEvent.NavigateTo(AppRoute.Home.route))
                    }
                    AuthResult.Cancelled -> {
                        _uiState.value = _uiState.value.copy(activeOperation = null)
                    }
                    is AuthResult.Failure -> {
                        _uiState.value =
                            _uiState.value.copy(
                                activeOperation = null,
                                message = mapGoogleError(result.reason),
                            )
                    }
                    is AuthResult.LinkRequired -> {
                        enterGoogleLinkingMode(result)
                    }
                }
            }
        }

        fun isSignedIn(): Boolean = authProvider.isSignedIn()

        override fun onCleared() {
            clearPendingGoogleCredentialOnly()
            super.onCleared()
        }

        private suspend fun submitCredentials(): AuthResult =
            if (_uiState.value.mode == AuthMode.SignIn) {
                authProvider.signIn(
                    email = _uiState.value.email.trim(),
                    password = _uiState.value.password,
                )
            } else {
                authProvider.createAccount(
                    email = _uiState.value.email.trim(),
                    password = _uiState.value.password,
                )
            }

        private fun validateCreateAccount(state: AuthUiState): Map<AuthField, AuthMessage> {
            val errors = mutableMapOf<AuthField, AuthMessage>()
            if (state.email.isBlank()) {
                errors[AuthField.Email] = AuthMessage.EmailRequired
            }
            if (state.password.length < 6) {
                errors[AuthField.Password] = AuthMessage.PasswordTooShort
            }
            if (state.confirmPassword != state.password) {
                errors[AuthField.ConfirmPassword] = AuthMessage.PasswordsDoNotMatch
            }
            return errors
        }

        private fun mapError(
            mode: AuthMode,
            reason: AuthFailureReason,
        ): AuthMessage =
            when (mode) {
                AuthMode.SignIn ->
                    when (reason) {
                        AuthFailureReason.InvalidEmail -> AuthMessage.InvalidEmail
                        AuthFailureReason.InvalidCredentials -> AuthMessage.InvalidCredentials
                        else -> AuthMessage.SignInFailed
                    }
                AuthMode.CreateAccount ->
                    when (reason) {
                        AuthFailureReason.WeakPassword -> AuthMessage.PasswordTooShort
                        AuthFailureReason.EmailAlreadyInUse -> AuthMessage.EmailAlreadyInUse
                        AuthFailureReason.InvalidEmail -> AuthMessage.InvalidEmail
                        else -> AuthMessage.CreateAccountFailed
                    }
            }

        private fun mapGoogleError(reason: AuthFailureReason): AuthMessage =
            when (reason) {
                AuthFailureReason.NoGoogleCredential -> AuthMessage.NoGoogleCredential
                AuthFailureReason.GoogleProviderUnavailable -> AuthMessage.GoogleProviderUnavailable
                AuthFailureReason.MissingGoogleIdToken -> AuthMessage.MissingGoogleIdToken
                AuthFailureReason.FirebaseSignInFailed -> AuthMessage.GoogleSignInFailed
                else -> AuthMessage.GoogleSignInFailed
            }

        private fun enterGoogleLinkingMode(result: AuthResult.LinkRequired) {
            pendingGoogleCredential = result.pendingCredential
            _uiState.value =
                _uiState.value.copy(
                    mode = AuthMode.SignIn,
                    email = result.email ?: _uiState.value.email,
                    confirmPassword = "",
                    activeOperation = null,
                    isLinkingGoogle = true,
                    linkingGoogleEmail = result.email,
                    message = AuthMessage.GoogleLinkRequired,
                    validationMessages = emptyMap(),
                )
        }

        private fun clearPendingGoogleLink() {
            clearPendingGoogleCredentialOnly()
            _uiState.value =
                _uiState.value.copy(
                    isLinkingGoogle = false,
                    linkingGoogleEmail = null,
                )
        }

        private fun clearPendingGoogleCredentialOnly() {
            pendingGoogleCredential = null
        }

        private fun isGoogleLinkingActive(): Boolean = pendingGoogleCredential != null
    }
