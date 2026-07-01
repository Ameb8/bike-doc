package com.bikedoc.android.navigation

import androidx.lifecycle.ViewModel
import com.bikedoc.android.auth.AuthProvider
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject

@HiltViewModel
class AppNavViewModel
    @Inject
    constructor(
        authProvider: AuthProvider,
    ) : ViewModel() {
        private val _startRoute =
            MutableStateFlow(
                if (authProvider.isSignedIn()) {
                    AppRoute.Home.route
                } else {
                    AppRoute.Auth.route
                },
            )
        val startRoute: StateFlow<String> = _startRoute.asStateFlow()
    }
