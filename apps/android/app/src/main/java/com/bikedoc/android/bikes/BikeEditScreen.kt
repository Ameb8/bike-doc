package com.bikedoc.android.bikes

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R

@Composable
fun BikeEditScreen(
    viewModel: BikeEditViewModel,
    onNavigateBack: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsState()
    BikeEditContent(
        state = uiState,
        onNavigateBack = onNavigateBack,
        onRetry = viewModel::retry,
        onSave = viewModel::save,
        onDisplayNameChanged = viewModel::onDisplayNameChanged,
        onMakeChanged = viewModel::onMakeChanged,
        onModelChanged = viewModel::onModelChanged,
        onModelYearChanged = viewModel::onModelYearChanged,
        onBikeTypeChanged = viewModel::onBikeTypeChanged,
        onFrameMaterialChanged = viewModel::onFrameMaterialChanged,
        onDrivetrainChanged = viewModel::onDrivetrainChanged,
        onBrakeTypeChanged = viewModel::onBrakeTypeChanged,
        onWheelSizeChanged = viewModel::onWheelSizeChanged,
        onTireSizeChanged = viewModel::onTireSizeChanged,
        onNotesChanged = viewModel::onNotesChanged,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BikeEditContent(
    state: BikeEditUiState,
    onNavigateBack: () -> Unit,
    onRetry: () -> Unit,
    onSave: () -> Unit,
    onDisplayNameChanged: (String) -> Unit,
    onMakeChanged: (String) -> Unit,
    onModelChanged: (String) -> Unit,
    onModelYearChanged: (String) -> Unit,
    onBikeTypeChanged: (String) -> Unit,
    onFrameMaterialChanged: (String) -> Unit,
    onDrivetrainChanged: (String) -> Unit,
    onBrakeTypeChanged: (String) -> Unit,
    onWheelSizeChanged: (String) -> Unit,
    onTireSizeChanged: (String) -> Unit,
    onNotesChanged: (String) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text =
                            stringResource(
                                if (state.isNew) {
                                    R.string.bike_edit_title_add
                                } else {
                                    R.string.bike_edit_title_edit
                                },
                            ),
                    )
                },
                navigationIcon = {
                    TextButton(
                        onClick = onNavigateBack,
                        enabled = !state.isSaving,
                    ) {
                        Text(text = stringResource(R.string.bike_edit_back))
                    }
                },
                actions = {
                    if (state.isSaving) {
                        CircularProgressIndicator(
                            modifier = Modifier.padding(end = 16.dp),
                        )
                    } else {
                        TextButton(
                            onClick = onSave,
                            enabled = !state.isLoading,
                        ) {
                            Text(text = stringResource(R.string.bike_edit_save))
                        }
                    }
                },
            )
        },
    ) { padding ->
        when {
            state.isLoading -> BikeEditLoadingState(padding = padding)
            state.error != null && !state.isSaving && !state.isNew && state.displayName.isBlank() ->
                BikeEditErrorState(
                    padding = padding,
                    message = state.error,
                    onRetry = onRetry,
                )

            else ->
                BikeEditForm(
                    state = state,
                    padding = padding,
                    onDisplayNameChanged = onDisplayNameChanged,
                    onMakeChanged = onMakeChanged,
                    onModelChanged = onModelChanged,
                    onModelYearChanged = onModelYearChanged,
                    onBikeTypeChanged = onBikeTypeChanged,
                    onFrameMaterialChanged = onFrameMaterialChanged,
                    onDrivetrainChanged = onDrivetrainChanged,
                    onBrakeTypeChanged = onBrakeTypeChanged,
                    onWheelSizeChanged = onWheelSizeChanged,
                    onTireSizeChanged = onTireSizeChanged,
                    onNotesChanged = onNotesChanged,
                )
        }
    }
}

@Composable
private fun BikeEditLoadingState(padding: PaddingValues) {
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
private fun BikeEditErrorState(
    padding: PaddingValues,
    message: String,
    onRetry: () -> Unit,
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
            Text(text = stringResource(R.string.bike_edit_load_error_title))
            Text(text = message)
            TextButton(onClick = onRetry) {
                Text(text = stringResource(R.string.bike_edit_retry))
            }
        }
    }
}

@Composable
private fun BikeEditForm(
    state: BikeEditUiState,
    padding: PaddingValues,
    onDisplayNameChanged: (String) -> Unit,
    onMakeChanged: (String) -> Unit,
    onModelChanged: (String) -> Unit,
    onModelYearChanged: (String) -> Unit,
    onBikeTypeChanged: (String) -> Unit,
    onFrameMaterialChanged: (String) -> Unit,
    onDrivetrainChanged: (String) -> Unit,
    onBrakeTypeChanged: (String) -> Unit,
    onWheelSizeChanged: (String) -> Unit,
    onTireSizeChanged: (String) -> Unit,
    onNotesChanged: (String) -> Unit,
) {
    val formEnabled = !state.isSaving && !state.isLoading

    Column(
        modifier =
            Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        state.error?.let {
            Text(text = it)
        }

        BikeEditTextField(
            value = state.displayName,
            onValueChange = onDisplayNameChanged,
            label = stringResource(R.string.bike_edit_display_name),
            enabled = formEnabled,
            error = state.validationErrors["displayName"],
        )
        BikeEditTextField(
            value = state.make,
            onValueChange = onMakeChanged,
            label = stringResource(R.string.bike_edit_make),
            enabled = formEnabled,
        )
        BikeEditTextField(
            value = state.model,
            onValueChange = onModelChanged,
            label = stringResource(R.string.bike_edit_model),
            enabled = formEnabled,
        )
        BikeEditTextField(
            value = state.modelYear,
            onValueChange = onModelYearChanged,
            label = stringResource(R.string.bike_edit_model_year),
            enabled = formEnabled,
            error = state.validationErrors["modelYear"],
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        )
        BikeEditDropdownField(
            value = state.bikeType,
            label = stringResource(R.string.bike_edit_bike_type),
            enabled = formEnabled,
            options = bikeTypeOptions(),
            onValueSelected = onBikeTypeChanged,
        )
        BikeEditDropdownField(
            value = state.frameMaterial,
            label = stringResource(R.string.bike_edit_frame_material),
            enabled = formEnabled,
            options = frameMaterialOptions(),
            onValueSelected = onFrameMaterialChanged,
        )
        BikeEditTextField(
            value = state.drivetrain,
            onValueChange = onDrivetrainChanged,
            label = stringResource(R.string.bike_edit_drivetrain),
            enabled = formEnabled,
        )
        BikeEditDropdownField(
            value = state.brakeType,
            label = stringResource(R.string.bike_edit_brake_type),
            enabled = formEnabled,
            options = brakeTypeOptions(),
            onValueSelected = onBrakeTypeChanged,
        )
        BikeEditTextField(
            value = state.wheelSize,
            onValueChange = onWheelSizeChanged,
            label = stringResource(R.string.bike_edit_wheel_size),
            enabled = formEnabled,
        )
        BikeEditTextField(
            value = state.tireSize,
            onValueChange = onTireSizeChanged,
            label = stringResource(R.string.bike_edit_tire_size),
            enabled = formEnabled,
        )
        BikeEditTextField(
            value = state.notes,
            onValueChange = onNotesChanged,
            label = stringResource(R.string.bike_edit_notes),
            enabled = formEnabled,
            singleLine = false,
            minLines = 4,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
        )
    }
}

@Composable
private fun BikeEditTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    enabled: Boolean,
    error: String? = null,
    singleLine: Boolean = true,
    minLines: Int = 1,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        enabled = enabled,
        label = { Text(text = label) },
        isError = error != null,
        supportingText =
            error?.let {
                { Text(text = it) }
            },
        singleLine = singleLine,
        minLines = minLines,
        keyboardOptions = keyboardOptions,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BikeEditDropdownField(
    value: String,
    label: String,
    enabled: Boolean,
    options: List<BikeEditOption>,
    onValueSelected: (String) -> Unit,
) {
    val expanded = remember { mutableStateOf(false) }
    val selectedLabel = options.firstOrNull { it.value == value }?.label ?: value

    ExposedDropdownMenuBox(
        expanded = expanded.value,
        onExpandedChange = {
            if (enabled) {
                expanded.value = !expanded.value
            }
        },
    ) {
        OutlinedTextField(
            value = selectedLabel,
            onValueChange = {},
            readOnly = true,
            enabled = enabled,
            modifier =
                Modifier
                    .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                    .fillMaxWidth(),
            label = { Text(text = label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded.value) },
        )
        DropdownMenu(
            expanded = expanded.value,
            onDismissRequest = { expanded.value = false },
        ) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(text = option.label) },
                    onClick = {
                        expanded.value = false
                        onValueSelected(option.value)
                    },
                )
            }
        }
    }
}

private data class BikeEditOption(
    val value: String,
    val label: String,
)

@Composable
private fun bikeTypeOptions(): List<BikeEditOption> =
    listOf(
        BikeEditOption("unknown", stringResource(R.string.bike_edit_option_unknown)),
        BikeEditOption("road", stringResource(R.string.bike_edit_option_road)),
        BikeEditOption("gravel", stringResource(R.string.bike_edit_option_gravel)),
        BikeEditOption("mountain", stringResource(R.string.bike_edit_option_mountain)),
        BikeEditOption("hybrid", stringResource(R.string.bike_edit_option_hybrid)),
        BikeEditOption("commuter", stringResource(R.string.bike_edit_option_commuter)),
        BikeEditOption("cargo", stringResource(R.string.bike_edit_option_cargo)),
        BikeEditOption("ebike", stringResource(R.string.bike_edit_option_ebike)),
        BikeEditOption("other", stringResource(R.string.bike_edit_option_other)),
    )

@Composable
private fun frameMaterialOptions(): List<BikeEditOption> =
    listOf(
        BikeEditOption("unknown", stringResource(R.string.bike_edit_option_unknown)),
        BikeEditOption("aluminum", stringResource(R.string.bike_edit_option_aluminum)),
        BikeEditOption("steel", stringResource(R.string.bike_edit_option_steel)),
        BikeEditOption("carbon", stringResource(R.string.bike_edit_option_carbon)),
        BikeEditOption("titanium", stringResource(R.string.bike_edit_option_titanium)),
        BikeEditOption("other", stringResource(R.string.bike_edit_option_other)),
    )

@Composable
private fun brakeTypeOptions(): List<BikeEditOption> =
    listOf(
        BikeEditOption("unknown", stringResource(R.string.bike_edit_option_unknown)),
        BikeEditOption("rim", stringResource(R.string.bike_edit_option_rim)),
        BikeEditOption("mechanical_disc", stringResource(R.string.bike_edit_option_mechanical_disc)),
        BikeEditOption("hydraulic_disc", stringResource(R.string.bike_edit_option_hydraulic_disc)),
        BikeEditOption("coaster", stringResource(R.string.bike_edit_option_coaster)),
        BikeEditOption("other", stringResource(R.string.bike_edit_option_other)),
    )
