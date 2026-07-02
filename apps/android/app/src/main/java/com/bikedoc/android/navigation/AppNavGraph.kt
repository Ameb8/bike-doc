package com.bikedoc.android.navigation

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.navigation.NavGraphBuilder
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.bikedoc.android.R
import com.bikedoc.android.auth.AuthScreen
import com.bikedoc.android.auth.AuthViewModel
import com.bikedoc.android.bikes.BIKE_LIST_REFRESH_REQUESTED
import com.bikedoc.android.bikes.BikeEditScreen
import com.bikedoc.android.bikes.BikeEditViewModel
import com.bikedoc.android.bikes.BikeListScreen
import com.bikedoc.android.bikes.BikeListViewModel
import com.bikedoc.android.home.HomeScreen
import com.bikedoc.android.home.HomeViewModel

@Composable
fun AppNavGraph(
    navController: NavHostController = rememberNavController(),
    viewModel: AppNavViewModel = hiltViewModel(),
) {
    val startRoute by viewModel.startRoute.collectAsState()

    NavHost(
        navController = navController,
        startDestination = startRoute,
    ) {
        authDestination(navController)
        homeDestination(navController)
        bikeDestinations(navController)
        appDestinations(navController)
    }
}

private fun NavGraphBuilder.authDestination(navController: NavHostController) {
    composable(AppRoute.Auth.route) {
        val authViewModel: AuthViewModel = hiltViewModel()
        LaunchedEffect(authViewModel) {
            authViewModel.events.collect { event ->
                if (event is UiEvent.NavigateTo && event.route == AppRoute.Home.route) {
                    navController.navigate(AppRoute.Home.route) {
                        popUpTo(AppRoute.Auth.route) { inclusive = true }
                    }
                }
            }
        }
        AuthScreen(viewModel = authViewModel)
    }
}

private fun NavGraphBuilder.homeDestination(navController: NavHostController) {
    composable(AppRoute.Home.route) {
        val homeViewModel: HomeViewModel = hiltViewModel()
        LaunchedEffect(homeViewModel) {
            homeViewModel.events.collect { event ->
                when (event) {
                    is UiEvent.NavigateTo -> navController.handleNavigation(event)
                    UiEvent.NavigateBack -> navController.popBackStack()
                    is UiEvent.NavigateBackWithResult -> Unit
                    is UiEvent.ShowSnackbar -> Unit
                }
            }
        }
        HomeScreen(viewModel = homeViewModel)
    }
}

private fun NavHostController.handleNavigation(event: UiEvent.NavigateTo) {
    if (event.route == AppRoute.Auth.route) {
        navigate(AppRoute.Auth.route) {
            popUpTo(AppRoute.Home.route) { inclusive = true }
        }
    } else {
        navigate(event.route)
    }
}

private fun NavGraphBuilder.bikeDestinations(navController: NavHostController) {
    composable(
        route = AppRoute.Bikes.route,
        arguments =
            listOf(
                navArgument("selectionMode") {
                    type = NavType.BoolType
                    defaultValue = false
                },
            ),
    ) {
        val bikeListViewModel: BikeListViewModel = hiltViewModel()
        val refreshRequested by
            it.savedStateHandle
                .getStateFlow(BIKE_LIST_REFRESH_REQUESTED, false)
                .collectAsState()
        LaunchedEffect(refreshRequested) {
            if (refreshRequested) {
                bikeListViewModel.refresh()
                it.savedStateHandle[BIKE_LIST_REFRESH_REQUESTED] = false
            }
        }
        BikeListScreen(
            viewModel = bikeListViewModel,
            onAddBike = { navController.navigate(AppRoute.BikeNew.route) },
            onOpenBike = { bikeId -> navController.navigate(AppRoute.BikeEdit.create(bikeId)) },
        )
    }
}

private fun NavGraphBuilder.appDestinations(navController: NavHostController) {
    composable(AppRoute.BikeNew.route) {
        val bikeEditViewModel: BikeEditViewModel = hiltViewModel()
        LaunchedEffect(bikeEditViewModel) {
            bikeEditViewModel.events.collect { event ->
                when (event) {
                    UiEvent.NavigateBack -> navController.popBackStack()
                    is UiEvent.NavigateBackWithResult -> navController.navigateBackWithResult(event)
                    is UiEvent.NavigateTo -> navController.navigate(event.route)
                    is UiEvent.ShowSnackbar -> Unit
                }
            }
        }
        BikeEditScreen(
            viewModel = bikeEditViewModel,
            onNavigateBack = { navController.popBackStack() },
        )
    }

    composable(
        route = AppRoute.BikeEdit.route,
        arguments =
            listOf(
                navArgument("bikeId") {
                    type = NavType.StringType
                },
            ),
    ) {
        val bikeEditViewModel: BikeEditViewModel = hiltViewModel()
        LaunchedEffect(bikeEditViewModel) {
            bikeEditViewModel.events.collect { event ->
                when (event) {
                    UiEvent.NavigateBack -> navController.popBackStack()
                    is UiEvent.NavigateBackWithResult -> navController.navigateBackWithResult(event)
                    is UiEvent.NavigateTo -> navController.navigate(event.route)
                    is UiEvent.ShowSnackbar -> Unit
                }
            }
        }
        BikeEditScreen(
            viewModel = bikeEditViewModel,
            onNavigateBack = { navController.popBackStack() },
        )
    }

    composable(AppRoute.DiagnosticChat.route) {
        PlaceholderScreen()
    }
}

private fun NavHostController.navigateBackWithResult(event: UiEvent.NavigateBackWithResult) {
    previousBackStackEntry?.savedStateHandle?.set(event.key, event.value)
    popBackStack()
}

@Composable
private fun PlaceholderScreen() {
    Scaffold { padding ->
        Box(
            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(padding),
            contentAlignment = Alignment.Center,
        ) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                CircularProgressIndicator()
                Text(text = stringResource(R.string.not_implemented))
            }
        }
    }
}
