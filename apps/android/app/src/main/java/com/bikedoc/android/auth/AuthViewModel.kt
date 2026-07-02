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
    val isLoading: Boolean = false,
    val error: String? = null,
    val validationErrors: Map<String, String> = emptyMap(),
)

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

        fun onModeSelected(mode: AuthMode) {
            _uiState.value =
                _uiState.value.copy(
                    mode = mode,
                    error = null,
                    validationErrors = emptyMap(),
                )
        }

        fun onEmailChanged(email: String) {
            _uiState.value =
                _uiState.value.copy(
                    email = email,
                    error = null,
                    validationErrors = _uiState.value.validationErrors - "email",
                )
        }

        fun onPasswordChanged(password: String) {
            _uiState.value =
                _uiState.value.copy(
                    password = password,
                    error = null,
                    validationErrors = _uiState.value.validationErrors - "password",
                )
        }

        fun onConfirmPasswordChanged(confirmPassword: String) {
            _uiState.value =
                _uiState.value.copy(
                    confirmPassword = confirmPassword,
                    error = null,
                    validationErrors = _uiState.value.validationErrors - "confirmPassword",
                )
        }

        fun submit() {
            viewModelScope.launch {
                val currentState = _uiState.value
                val validationErrors =
                    if (currentState.mode == AuthMode.CreateAccount) {
                        validateCreateAccount(currentState)
                    } else {
                        emptyMap()
                    }

                if (validationErrors.isNotEmpty()) {
                    _uiState.value =
                        currentState.copy(
                            isLoading = false,
                            error = null,
                            validationErrors = validationErrors,
                        )
                    return@launch
                }

                _uiState.value =
                    currentState.copy(
                        isLoading = true,
                        error = null,
                        validationErrors = emptyMap(),
                    )

                when (val result = submitCredentials()) {
                    AuthResult.Success -> {
                        _uiState.value = _uiState.value.copy(isLoading = false)
                        eventChannel.send(UiEvent.NavigateTo(AppRoute.Home.route))
                    }
                    is AuthResult.Failure -> {
                        _uiState.value =
                            _uiState.value.copy(
                                isLoading = false,
                                error = mapError(_uiState.value.mode, result.reason),
                            )
                    }
                }
            }
        }

        fun isSignedIn(): Boolean = authProvider.isSignedIn()

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

        private fun validateCreateAccount(state: AuthUiState): Map<String, String> {
            val errors = mutableMapOf<String, String>()
            if (state.email.isBlank()) {
                errors["email"] = "Email is required."
            }
            if (state.password.length < 6) {
                errors["password"] = "Password must be at least 6 characters."
            }
            if (state.confirmPassword != state.password) {
                errors["confirmPassword"] = "Passwords do not match."
            }
            return errors
        }

        private fun mapError(
            mode: AuthMode,
            reason: AuthFailureReason,
        ): String =
            when (mode) {
                AuthMode.SignIn ->
                    when (reason) {
                        AuthFailureReason.InvalidEmail -> "Enter a valid email address."
                        AuthFailureReason.InvalidCredentials -> "Incorrect email or password."
                        else -> "Sign in failed. Please try again."
                    }
                AuthMode.CreateAccount ->
                    when (reason) {
                        AuthFailureReason.WeakPassword -> "Password must be at least 6 characters."
                        AuthFailureReason.EmailAlreadyInUse -> "An account with this email already exists."
                        AuthFailureReason.InvalidEmail -> "Enter a valid email address."
                        else -> "Account creation failed. Please try again."
                    }
            }
    }
