@file:Suppress("MaxLineLength")

package com.bikedoc.android.bikes

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.bikedoc.android.R

@Composable
fun BikeEditScreen(
    viewModel: BikeEditViewModel,
    onNavigateBack: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsState()
    BikeEditContent(uiState, onNavigateBack, viewModel::retry, viewModel::save, viewModel::onProfileChanged)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BikeEditContent(
    state: BikeEditUiState,
    onNavigateBack: () -> Unit,
    onRetry: () -> Unit,
    onSave: () -> Unit,
    onProfileChanged: (BikeProfileEdit) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(if (state.isNew) R.string.bike_edit_title_add else R.string.bike_edit_title_edit)) },
                navigationIcon = {
                    TextButton(
                        onNavigateBack,
                        enabled = !state.isSaving,
                    ) { Text(stringResource(R.string.bike_edit_back)) }
                },
                actions = {
                    if (state.isSaving) {
                        CircularProgressIndicator(modifier = Modifier.padding(end = 16.dp))
                    } else {
                        TextButton(onSave, enabled = !state.isLoading) { Text(stringResource(R.string.bike_edit_save)) }
                    }
                },
            )
        },
    ) { padding ->
        when {
            state.isLoading -> LoadingState(padding)
            state.error != null && !state.isNew -> ErrorState(padding, state.error, onRetry)
            else -> ProfileForm(state, padding, onProfileChanged)
        }
    }
}

@Composable
private fun LoadingState(padding: PaddingValues) =
    Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) { CircularProgressIndicator() }

@Composable
private fun ErrorState(
    padding: PaddingValues,
    message: String,
    onRetry: () -> Unit,
) = Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(stringResource(R.string.bike_edit_load_error_title))
        Text(message)
        TextButton(onRetry) { Text(stringResource(R.string.bike_edit_retry)) }
    }
}

@Composable
@Suppress("LongMethod")
private fun ProfileForm(
    state: BikeEditUiState,
    padding: PaddingValues,
    onChanged: (BikeProfileEdit) -> Unit,
) {
    val profile = state.profile
    val enabled = !state.isSaving && !state.isLoading
    Column(
        Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        state.error?.let { Text(it) }
        Field(profile.displayName, {
            onChanged(profile.copy(displayName = it))
        }, stringResource(R.string.bike_edit_display_name), enabled, state.validationErrors["displayName"])
        Section("Identity and frame") {
            val identity = profile.identity
            Field(identity.make.orEmpty(), { onChanged(profile.copy(identity = identity.copy(make = it.blankToNull()))) }, "Make", enabled)
            Field(
                identity.model.orEmpty(),
                { onChanged(profile.copy(identity = identity.copy(model = it.blankToNull()))) },
                "Model",
                enabled,
            )
            IntField(identity.modelYear, { onChanged(profile.copy(identity = identity.copy(modelYear = it))) }, "Model year", enabled)
            Field(identity.bikeType.orEmpty(), {
                onChanged(profile.copy(identity = identity.copy(bikeType = it.blankToNull())))
            }, "Bike type (road, gravel, mountain…)", enabled)
            val frame = profile.frame
            Field(
                frame.material.orEmpty(),
                { onChanged(profile.copy(frame = frame.copy(material = it.blankToNull()))) },
                "Frame material",
                enabled,
            )
            Field(
                frame.sizeLabel.orEmpty(),
                { onChanged(profile.copy(frame = frame.copy(sizeLabel = it.blankToNull()))) },
                "Frame size",
                enabled,
            )
            Field(frame.primaryColor.orEmpty(), {
                onChanged(profile.copy(frame = frame.copy(primaryColor = it.blankToNull())))
            }, "Primary color", enabled)
            Field(frame.secondaryColor.orEmpty(), {
                onChanged(profile.copy(frame = frame.copy(secondaryColor = it.blankToNull())))
            }, "Secondary color", enabled)
        }
        Section("Brakes") {
            BrakeFields(
                "Front brake",
                profile.brakes.front,
                enabled,
            ) { front -> onChanged(profile.copy(brakes = profile.brakes.copy(front = front))) }
            HorizontalDivider()
            BrakeFields(
                "Rear brake",
                profile.brakes.rear,
                enabled,
            ) { rear -> onChanged(profile.copy(brakes = profile.brakes.copy(rear = rear))) }
        }
        Section("Drivetrain") {
            val drivetrain = profile.drivetrain
            Field(drivetrain.architecture.orEmpty(), {
                onChanged(profile.copy(drivetrain = drivetrain.copy(architecture = it.blankToNull())))
            }, "Architecture", enabled)
            Field(drivetrain.driveMedium.orEmpty(), {
                onChanged(profile.copy(drivetrain = drivetrain.copy(driveMedium = it.blankToNull())))
            }, "Drive medium", enabled)
            RoleFields(
                "Front shifter",
                drivetrain.frontShifter,
                enabled,
            ) { onChanged(profile.copy(drivetrain = drivetrain.copy(frontShifter = it))) }
            RoleFields(
                "Rear shifter",
                drivetrain.rearShifter,
                enabled,
            ) { onChanged(profile.copy(drivetrain = drivetrain.copy(rearShifter = it))) }
            RoleFields("Front derailleur", drivetrain.frontDerailleur, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(frontDerailleur = it)))
            }
            RoleFields("Rear derailleur", drivetrain.rearDerailleur, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(rearDerailleur = it)))
            }
            RoleFields("Crankset", drivetrain.crankset, enabled) { onChanged(profile.copy(drivetrain = drivetrain.copy(crankset = it))) }
            RoleFields(
                "Rear cluster",
                drivetrain.rearCluster,
                enabled,
            ) { onChanged(profile.copy(drivetrain = drivetrain.copy(rearCluster = it))) }
            RoleFields("Chain", drivetrain.chain, enabled) { onChanged(profile.copy(drivetrain = drivetrain.copy(chain = it))) }
            RoleFields("Belt", drivetrain.belt, enabled) { onChanged(profile.copy(drivetrain = drivetrain.copy(belt = it))) }
            RoleFields("Gear unit", drivetrain.gearUnit, enabled) { onChanged(profile.copy(drivetrain = drivetrain.copy(gearUnit = it))) }
            RoleFields("Bottom bracket", drivetrain.bottomBracket, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(bottomBracket = it)))
            }
        }
        Section("Wheels and tires") {
            WheelFields(
                "Front wheel",
                profile.rollingSystem.front,
                false,
                enabled,
            ) { front -> onChanged(profile.copy(rollingSystem = profile.rollingSystem.copy(front = front))) }
            HorizontalDivider()
            WheelFields(
                "Rear wheel",
                profile.rollingSystem.rear,
                true,
                enabled,
            ) { rear -> onChanged(profile.copy(rollingSystem = profile.rollingSystem.copy(rear = rear))) }
        }
        Section("Suspension") { SuspensionFields(profile, enabled, onChanged) }
        Section("Cockpit and seating") { CockpitFields(profile, enabled, onChanged) }
        Section("Electric assist") { ElectricFields(profile, enabled, onChanged) }
        Field(profile.notes.orEmpty(), {
            onChanged(profile.copy(notes = it.blankToNull()))
        }, stringResource(R.string.bike_edit_notes), enabled, singleLine = false)
    }
}

@Composable
private fun BrakeFields(
    label: String,
    brake: BrakeAssembly,
    enabled: Boolean,
    onChanged: (BrakeAssembly) -> Unit,
) {
    Text(label)
    PresenceFields(brake.component, enabled) { onChanged(brake.copy(component = it)) }
    Field(brake.mechanism.orEmpty(), { onChanged(brake.copy(mechanism = it.blankToNull())) }, "Mechanism", enabled)
    Field(brake.actuation.orEmpty(), { onChanged(brake.copy(actuation = it.blankToNull())) }, "Actuation", enabled)
    val rotor = brake.rotor ?: Rotor()
    Field(rotor.component.manufacturer.orEmpty(), {
        onChanged(brake.copy(rotor = rotor.copy(component = rotor.component.copy(manufacturer = it.blankToNull()))))
    }, "Rotor manufacturer", enabled)
    IntField(rotor.diameterMm?.toInt(), {
        onChanged(brake.copy(rotor = rotor.copy(diameterMm = it?.toDouble())))
    }, "Rotor diameter (mm)", enabled)
}

@Composable
private fun RoleFields(
    label: String,
    role: DrivetrainRole?,
    enabled: Boolean,
    onChanged: (DrivetrainRole) -> Unit,
) {
    val value = role ?: DrivetrainRole()
    Text(label)
    PresenceFields(value.component, enabled) { onChanged(value.copy(component = it)) }
    Field(value.actuation.orEmpty(), { onChanged(value.copy(actuation = it.blankToNull())) }, "Actuation", enabled)
    IntField(value.speedCount, { onChanged(value.copy(speedCount = it)) }, "Speed count", enabled)
    Field(value.mountType.orEmpty(), { onChanged(value.copy(mountType = it.blankToNull())) }, "Mount type", enabled)
    IntField(value.chainringCount, { onChanged(value.copy(chainringCount = it)) }, "Chainring count", enabled)
    Field(
        value.chainringToothCounts.orEmpty(),
        { onChanged(value.copy(chainringToothCounts = it.blankToNull())) },
        "Chainring teeth",
        enabled,
    )
    Field(value.clusterType.orEmpty(), { onChanged(value.copy(clusterType = it.blankToNull())) }, "Cluster type", enabled)
    Field(value.driverInterface.orEmpty(), { onChanged(value.copy(driverInterface = it.blankToNull())) }, "Driver interface", enabled)
}

@Composable
private fun WheelFields(
    label: String,
    position: WheelPosition,
    isRear: Boolean,
    enabled: Boolean,
    onChanged: (WheelPosition) -> Unit,
) {
    Text(label)
    val wheel = position.wheel ?: WheelComponent()
    PresenceFields(wheel.component, enabled) { onChanged(position.copy(wheel = wheel.copy(component = it))) }
    Field(
        wheel.nominalSize.orEmpty(),
        { onChanged(position.copy(wheel = wheel.copy(nominalSize = it.blankToNull()))) },
        "Wheel size",
        enabled,
    )
    val tire = position.tire ?: TireComponent()
    Field(tire.component.manufacturer.orEmpty(), {
        onChanged(position.copy(tire = tire.copy(component = tire.component.copy(manufacturer = it.blankToNull()))))
    }, "Tire manufacturer", enabled)
    Field(
        tire.markedSize.orEmpty(),
        { onChanged(position.copy(tire = tire.copy(markedSize = it.blankToNull()))) },
        "Tire marked size",
        enabled,
    )
    Field(tire.setup.orEmpty(), { onChanged(position.copy(tire = tire.copy(setup = it.blankToNull()))) }, "Tire setup", enabled)
    val hub = position.hub ?: HubComponent()
    Field(hub.axleType.orEmpty(), { onChanged(position.copy(hub = hub.copy(axleType = it.blankToNull()))) }, "Hub axle type", enabled)
    if (isRear) {
        Field(hub.driverInterface.orEmpty(), {
            onChanged(position.copy(hub = hub.copy(driverInterface = it.blankToNull())))
        }, "Rear hub driver interface", enabled)
    }
}

@Composable
private fun SuspensionFields(
    profile: BikeProfileEdit,
    enabled: Boolean,
    onChanged: (BikeProfileEdit) -> Unit,
) {
    val suspension = profile.suspension
    val fork = suspension.fork ?: Fork()
    Field(fork.type.orEmpty(), {
        onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(type = it.blankToNull()))))
    }, "Fork type", enabled)
    Field(fork.manufacturer.orEmpty(), {
        onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(manufacturer = it.blankToNull()))))
    }, "Fork manufacturer", enabled)
    Field(fork.model.orEmpty(), {
        onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(model = it.blankToNull()))))
    }, "Fork model", enabled)
    IntField(fork.travelMm, {
        onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(travelMm = it))))
    }, "Fork travel (mm)", enabled)
    val rearShock = suspension.rearShock ?: ComponentIdentity()
    PresenceFields(rearShock, enabled) { onChanged(profile.copy(suspension = suspension.copy(rearShock = it))) }
    IntField(
        suspension.rearTravelMm,
        { onChanged(profile.copy(suspension = suspension.copy(rearTravelMm = it))) },
        "Rear travel (mm)",
        enabled,
    )
}

@Composable
private fun CockpitFields(
    profile: BikeProfileEdit,
    enabled: Boolean,
    onChanged: (BikeProfileEdit) -> Unit,
) {
    val cockpit = profile.cockpit
    val bar = cockpit.handlebar ?: Handlebar()
    Field(bar.style.orEmpty(), {
        onChanged(profile.copy(cockpit = cockpit.copy(handlebar = bar.copy(style = it.blankToNull()))))
    }, "Handlebar style", enabled)
    Field(bar.manufacturer.orEmpty(), {
        onChanged(profile.copy(cockpit = cockpit.copy(handlebar = bar.copy(manufacturer = it.blankToNull()))))
    }, "Handlebar manufacturer", enabled)
    val stem = cockpit.stem ?: Stem()
    Field(stem.type.orEmpty(), {
        onChanged(profile.copy(cockpit = cockpit.copy(stem = stem.copy(type = it.blankToNull()))))
    }, "Stem type", enabled)
    val seatpost = profile.seating.seatpost ?: Seatpost()
    Field(seatpost.type.orEmpty(), {
        onChanged(profile.copy(seating = profile.seating.copy(seatpost = seatpost.copy(type = it.blankToNull()))))
    }, "Seatpost type", enabled)
    IntField(seatpost.diameterMm?.toInt(), {
        onChanged(profile.copy(seating = profile.seating.copy(seatpost = seatpost.copy(diameterMm = it?.toDouble()))))
    }, "Seatpost diameter (mm)", enabled)
}

@Composable
private fun ElectricFields(
    profile: BikeProfileEdit,
    enabled: Boolean,
    onChanged: (BikeProfileEdit) -> Unit,
) {
    val assist = profile.electricAssist
    Field(assist.presence?.name?.lowercase().orEmpty(), {
        onChanged(profile.copy(electricAssist = assist.copy(presence = it.toPresence())))
    }, "Presence (unknown, present, absent)", enabled)
    Field(assist.systemManufacturer.orEmpty(), {
        onChanged(profile.copy(electricAssist = assist.copy(systemManufacturer = it.blankToNull())))
    }, "System manufacturer", enabled)
    Field(assist.systemModel.orEmpty(), {
        onChanged(profile.copy(electricAssist = assist.copy(systemModel = it.blankToNull())))
    }, "System model", enabled)
    val motor = assist.motor ?: ElectricMotor()
    Field(motor.position.orEmpty(), {
        onChanged(profile.copy(electricAssist = assist.copy(motor = motor.copy(position = it.blankToNull()))))
    }, "Motor position", enabled)
    val battery = assist.battery ?: ElectricBattery()
    Field(battery.model.orEmpty(), {
        onChanged(profile.copy(electricAssist = assist.copy(battery = battery.copy(model = it.blankToNull()))))
    }, "Battery model", enabled)
}

@Composable
private fun PresenceFields(
    identity: ComponentIdentity,
    enabled: Boolean,
    onChanged: (ComponentIdentity) -> Unit,
) {
    Field(identity.presence?.name?.lowercase().orEmpty(), {
        onChanged(identity.copy(presence = it.toPresence()))
    }, "Presence (unknown, present, absent)", enabled)
    Field(identity.manufacturer.orEmpty(), { onChanged(identity.copy(manufacturer = it.blankToNull())) }, "Manufacturer", enabled)
    Field(identity.model.orEmpty(), { onChanged(identity.copy(model = it.blankToNull())) }, "Model", enabled)
}

@Composable
private fun Section(
    title: String,
    content: @Composable () -> Unit,
) {
    Text(title)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) { content() }
}

@Composable
private fun Field(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    enabled: Boolean,
    error: String? = null,
    singleLine: Boolean = true,
) {
    OutlinedTextField(value, onValueChange, Modifier.fillMaxWidth(), enabled = enabled, label = {
        Text(label)
    }, isError = error != null, supportingText = error?.let { { Text(it) } }, singleLine = singleLine)
}

@Composable
private fun IntField(
    value: Int?,
    onValueChange: (Int?) -> Unit,
    label: String,
    enabled: Boolean,
) {
    Field(value?.toString().orEmpty(), { onValueChange(it.toIntOrNull()) }, label, enabled, singleLine = true)
}

private fun String.blankToNull() = trim().takeIf(String::isNotEmpty)

private fun String.toPresence() =
    when (lowercase()) {
        "unknown" -> ComponentPresence.UNKNOWN
        "present" -> ComponentPresence.PRESENT
        "absent" -> ComponentPresence.ABSENT
        else -> null
    }
