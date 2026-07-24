@file:Suppress("MaxLineLength")

package com.bikedoc.android.bikes

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
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
        CollapsibleSection(
            title = stringResource(R.string.bike_edit_identity_frame),
            summary =
                summary(
                    profile.identity.make,
                    profile.identity.model,
                    profile.frame.material,
                ),
        ) {
            val identity = profile.identity
            ComponentSection(stringResource(R.string.bike_edit_identity), summary(identity.make, identity.model)) {
                Field(identity.make.orEmpty(), {
                    onChanged(profile.copy(identity = identity.copy(make = it.blankToNull())))
                }, stringResource(R.string.bike_edit_make), enabled)
                Field(identity.model.orEmpty(), {
                    onChanged(profile.copy(identity = identity.copy(model = it.blankToNull())))
                }, stringResource(R.string.bike_edit_model), enabled)
                IntField(identity.modelYear, {
                    onChanged(profile.copy(identity = identity.copy(modelYear = it)))
                }, stringResource(R.string.bike_edit_model_year), enabled)
                Field(identity.bikeType.orEmpty(), {
                    onChanged(profile.copy(identity = identity.copy(bikeType = it.blankToNull())))
                }, stringResource(R.string.bike_edit_bike_type), enabled)
            }
            ComponentSection(stringResource(R.string.bike_edit_frame), summary(profile.frame.material, profile.frame.sizeLabel)) {
                val frame = profile.frame
                Field(frame.material.orEmpty(), {
                    onChanged(profile.copy(frame = frame.copy(material = it.blankToNull())))
                }, stringResource(R.string.bike_edit_frame_material), enabled)
                Field(frame.sizeLabel.orEmpty(), {
                    onChanged(profile.copy(frame = frame.copy(sizeLabel = it.blankToNull())))
                }, stringResource(R.string.bike_edit_frame_size), enabled)
                Field(frame.primaryColor.orEmpty(), {
                    onChanged(profile.copy(frame = frame.copy(primaryColor = it.blankToNull())))
                }, stringResource(R.string.bike_edit_primary_color), enabled)
                Field(frame.secondaryColor.orEmpty(), {
                    onChanged(profile.copy(frame = frame.copy(secondaryColor = it.blankToNull())))
                }, stringResource(R.string.bike_edit_secondary_color), enabled)
            }
        }
        CollapsibleSection(
            title = stringResource(R.string.bike_edit_brakes),
            summary = summary(profile.brakes.front.component.manufacturer, profile.brakes.front.component.model),
        ) {
            ComponentSection(
                stringResource(R.string.bike_edit_front_brake),
                summary(profile.brakes.front.component.manufacturer, profile.brakes.front.component.model),
            ) {
                BrakeFields(profile.brakes.front, enabled) { onChanged(profile.copy(brakes = profile.brakes.copy(front = it))) }
            }
            ComponentSection(
                stringResource(R.string.bike_edit_rear_brake),
                summary(profile.brakes.rear.component.manufacturer, profile.brakes.rear.component.model),
            ) {
                BrakeFields(profile.brakes.rear, enabled) { onChanged(profile.copy(brakes = profile.brakes.copy(rear = it))) }
            }
        }
        CollapsibleSection(
            title = stringResource(R.string.bike_edit_drivetrain),
            summary = summary(profile.drivetrain.architecture, profile.drivetrain.driveMedium),
        ) {
            val drivetrain = profile.drivetrain
            Field(drivetrain.architecture.orEmpty(), {
                onChanged(profile.copy(drivetrain = drivetrain.copy(architecture = it.blankToNull())))
            }, stringResource(R.string.bike_edit_architecture), enabled)
            Field(drivetrain.driveMedium.orEmpty(), {
                onChanged(profile.copy(drivetrain = drivetrain.copy(driveMedium = it.blankToNull())))
            }, stringResource(R.string.bike_edit_drive_medium), enabled)
            RoleSection(stringResource(R.string.bike_edit_front_shifter), drivetrain.frontShifter, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(frontShifter = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_rear_shifter), drivetrain.rearShifter, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(rearShifter = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_front_derailleur), drivetrain.frontDerailleur, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(frontDerailleur = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_rear_derailleur), drivetrain.rearDerailleur, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(rearDerailleur = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_crankset), drivetrain.crankset, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(crankset = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_rear_cluster), drivetrain.rearCluster, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(rearCluster = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_chain), drivetrain.chain, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(chain = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_belt), drivetrain.belt, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(belt = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_gear_unit), drivetrain.gearUnit, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(gearUnit = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_bottom_bracket), drivetrain.bottomBracket, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(bottomBracket = it)))
            }
        }
        CollapsibleSection(
            title = stringResource(R.string.bike_edit_wheels_tires),
            summary = summary(profile.rollingSystem.front.wheel?.nominalSize, profile.rollingSystem.front.tire?.markedSize),
        ) {
            ComponentSection(
                stringResource(R.string.bike_edit_front_wheel),
                summary(profile.rollingSystem.front.wheel?.nominalSize, profile.rollingSystem.front.tire?.markedSize),
            ) {
                WheelFields(
                    profile.rollingSystem.front,
                    false,
                    enabled,
                ) { onChanged(profile.copy(rollingSystem = profile.rollingSystem.copy(front = it))) }
            }
            ComponentSection(
                stringResource(R.string.bike_edit_rear_wheel),
                summary(profile.rollingSystem.rear.wheel?.nominalSize, profile.rollingSystem.rear.tire?.markedSize),
            ) {
                WheelFields(
                    profile.rollingSystem.rear,
                    true,
                    enabled,
                ) { onChanged(profile.copy(rollingSystem = profile.rollingSystem.copy(rear = it))) }
            }
        }
        CollapsibleSection(
            title = stringResource(R.string.bike_edit_suspension),
            summary = summary(profile.suspension.fork?.manufacturer, profile.suspension.fork?.model),
        ) { SuspensionFields(profile, enabled, onChanged) }
        CollapsibleSection(
            title = stringResource(R.string.bike_edit_cockpit_seating),
            summary = summary(profile.cockpit.handlebar?.style, profile.cockpit.stem?.type, profile.seating.seatpost?.type),
        ) { CockpitFields(profile, enabled, onChanged) }
        CollapsibleSection(
            title = stringResource(R.string.bike_edit_electric_assist),
            summary =
                summary(
                    profile.electricAssist.presence?.name?.lowercase(),
                    profile.electricAssist.systemManufacturer,
                    profile.electricAssist.systemModel,
                ),
        ) { ElectricFields(profile, enabled, onChanged) }
        Field(profile.notes.orEmpty(), {
            onChanged(profile.copy(notes = it.blankToNull()))
        }, stringResource(R.string.bike_edit_notes), enabled, singleLine = false)
    }
}

@Composable
private fun CollapsibleSection(
    title: String,
    summary: String,
    content: @Composable () -> Unit,
) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    SectionHeader(title, summary, expanded) { expanded = !expanded }
    if (expanded) Column(verticalArrangement = Arrangement.spacedBy(8.dp)) { content() }
}

@Composable
private fun ComponentSection(
    title: String,
    summary: String,
    content: @Composable () -> Unit,
) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    SectionHeader(title, summary, expanded) { expanded = !expanded }
    if (expanded) Column(verticalArrangement = Arrangement.spacedBy(8.dp)) { content() }
}

@Composable
private fun SectionHeader(
    title: String,
    summary: String,
    expanded: Boolean,
    onClick: () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        tonalElevation = 1.dp,
    ) {
        Row(modifier = Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(title)
                Text(summary, style = androidx.compose.material3.MaterialTheme.typography.bodySmall)
            }
            Text(if (expanded) "−" else "+")
        }
    }
}

@Composable
private fun summary(vararg values: String?): String =
    values.filterNot { it.isNullOrBlank() }.joinToString(" · ").ifBlank {
        stringResource(R.string.bike_edit_not_specified)
    }

@Composable
private fun RoleSection(
    label: String,
    role: DrivetrainRole?,
    enabled: Boolean,
    onChanged: (DrivetrainRole) -> Unit,
) {
    val value = role ?: DrivetrainRole()
    ComponentSection(label, summary(value.component.manufacturer, value.component.model, value.speedCount?.toString())) {
        RoleFields(value, enabled, onChanged)
    }
}

@Composable
private fun BrakeFields(
    brake: BrakeAssembly,
    enabled: Boolean,
    onChanged: (BrakeAssembly) -> Unit,
) {
    PresenceFields(brake.component, enabled) { onChanged(brake.copy(component = it)) }
    if (brake.component.presence != ComponentPresence.ABSENT) {
        Field(brake.mechanism.orEmpty(), {
            onChanged(brake.copy(mechanism = it.blankToNull()))
        }, stringResource(R.string.bike_edit_mechanism), enabled)
        Field(brake.actuation.orEmpty(), {
            onChanged(brake.copy(actuation = it.blankToNull()))
        }, stringResource(R.string.bike_edit_actuation), enabled)
        val rotor = brake.rotor ?: Rotor()
        Field(rotor.component.manufacturer.orEmpty(), {
            onChanged(brake.copy(rotor = rotor.copy(component = rotor.component.copy(manufacturer = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_rotor_manufacturer), enabled)
        IntField(rotor.diameterMm?.toInt(), {
            onChanged(brake.copy(rotor = rotor.copy(diameterMm = it?.toDouble())))
        }, stringResource(R.string.bike_edit_rotor_diameter), enabled)
    }
}

@Composable
private fun RoleFields(
    value: DrivetrainRole,
    enabled: Boolean,
    onChanged: (DrivetrainRole) -> Unit,
) {
    PresenceFields(value.component, enabled) { onChanged(value.copy(component = it)) }
    if (value.component.presence != ComponentPresence.ABSENT) {
        Field(value.actuation.orEmpty(), {
            onChanged(value.copy(actuation = it.blankToNull()))
        }, stringResource(R.string.bike_edit_actuation), enabled)
        IntField(value.speedCount, { onChanged(value.copy(speedCount = it)) }, stringResource(R.string.bike_edit_speed_count), enabled)
        Field(value.mountType.orEmpty(), {
            onChanged(value.copy(mountType = it.blankToNull()))
        }, stringResource(R.string.bike_edit_mount_type), enabled)
        IntField(
            value.chainringCount,
            { onChanged(value.copy(chainringCount = it)) },
            stringResource(R.string.bike_edit_chainring_count),
            enabled,
        )
        Field(value.chainringToothCounts.orEmpty(), {
            onChanged(value.copy(chainringToothCounts = it.blankToNull()))
        }, stringResource(R.string.bike_edit_chainring_teeth), enabled)
        Field(value.clusterType.orEmpty(), {
            onChanged(value.copy(clusterType = it.blankToNull()))
        }, stringResource(R.string.bike_edit_cluster_type), enabled)
        Field(value.driverInterface.orEmpty(), {
            onChanged(value.copy(driverInterface = it.blankToNull()))
        }, stringResource(R.string.bike_edit_driver_interface), enabled)
    }
}

@Composable
private fun WheelFields(
    position: WheelPosition,
    isRear: Boolean,
    enabled: Boolean,
    onChanged: (WheelPosition) -> Unit,
) {
    val wheel = position.wheel ?: WheelComponent()
    PresenceFields(wheel.component, enabled) { onChanged(position.copy(wheel = wheel.copy(component = it))) }
    if (wheel.component.presence != ComponentPresence.ABSENT) {
        Field(wheel.nominalSize.orEmpty(), {
            onChanged(position.copy(wheel = wheel.copy(nominalSize = it.blankToNull())))
        }, stringResource(R.string.bike_edit_wheel_size), enabled)
        val tire = position.tire ?: TireComponent()
        Field(tire.component.manufacturer.orEmpty(), {
            onChanged(position.copy(tire = tire.copy(component = tire.component.copy(manufacturer = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_tire_manufacturer), enabled)
        Field(tire.markedSize.orEmpty(), {
            onChanged(position.copy(tire = tire.copy(markedSize = it.blankToNull())))
        }, stringResource(R.string.bike_edit_tire_marked_size), enabled)
        Field(tire.setup.orEmpty(), {
            onChanged(position.copy(tire = tire.copy(setup = it.blankToNull())))
        }, stringResource(R.string.bike_edit_tire_setup), enabled)
        val hub = position.hub ?: HubComponent()
        Field(hub.axleType.orEmpty(), {
            onChanged(position.copy(hub = hub.copy(axleType = it.blankToNull())))
        }, stringResource(R.string.bike_edit_hub_axle_type), enabled)
        if (isRear) {
            Field(hub.driverInterface.orEmpty(), {
                onChanged(position.copy(hub = hub.copy(driverInterface = it.blankToNull())))
            }, stringResource(R.string.bike_edit_rear_hub_driver_interface), enabled)
        }
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
    ComponentSection(stringResource(R.string.bike_edit_fork), summary(fork.manufacturer, fork.model)) {
        Field(fork.type.orEmpty(), {
            onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(type = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_fork_type), enabled)
        Field(fork.manufacturer.orEmpty(), {
            onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(manufacturer = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_fork_manufacturer), enabled)
        Field(fork.model.orEmpty(), {
            onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(model = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_fork_model), enabled)
        IntField(fork.travelMm, {
            onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(travelMm = it))))
        }, stringResource(R.string.bike_edit_fork_travel), enabled)
    }
    val rearShock = suspension.rearShock ?: ComponentIdentity()
    ComponentSection(stringResource(R.string.bike_edit_rear_shock), summary(rearShock.manufacturer, rearShock.model)) {
        PresenceFields(rearShock, enabled) { onChanged(profile.copy(suspension = suspension.copy(rearShock = it))) }
        if (rearShock.presence != ComponentPresence.ABSENT) {
            IntField(suspension.rearTravelMm, {
                onChanged(profile.copy(suspension = suspension.copy(rearTravelMm = it)))
            }, stringResource(R.string.bike_edit_rear_travel), enabled)
        }
    }
}

@Composable
private fun CockpitFields(
    profile: BikeProfileEdit,
    enabled: Boolean,
    onChanged: (BikeProfileEdit) -> Unit,
) {
    val cockpit = profile.cockpit
    val bar = cockpit.handlebar ?: Handlebar()
    ComponentSection(stringResource(R.string.bike_edit_handlebar), summary(bar.style, bar.manufacturer)) {
        Field(bar.style.orEmpty(), {
            onChanged(profile.copy(cockpit = cockpit.copy(handlebar = bar.copy(style = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_handlebar_style), enabled)
        Field(bar.manufacturer.orEmpty(), {
            onChanged(profile.copy(cockpit = cockpit.copy(handlebar = bar.copy(manufacturer = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_handlebar_manufacturer), enabled)
    }
    val stem = cockpit.stem ?: Stem()
    ComponentSection(stringResource(R.string.bike_edit_stem), summary(stem.type)) {
        Field(stem.type.orEmpty(), {
            onChanged(profile.copy(cockpit = cockpit.copy(stem = stem.copy(type = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_stem_type), enabled)
    }
    val seatpost = profile.seating.seatpost ?: Seatpost()
    ComponentSection(stringResource(R.string.bike_edit_seatpost), summary(seatpost.type)) {
        Field(seatpost.type.orEmpty(), {
            onChanged(profile.copy(seating = profile.seating.copy(seatpost = seatpost.copy(type = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_seatpost_type), enabled)
        IntField(seatpost.diameterMm?.toInt(), {
            onChanged(profile.copy(seating = profile.seating.copy(seatpost = seatpost.copy(diameterMm = it?.toDouble()))))
        }, stringResource(R.string.bike_edit_seatpost_diameter), enabled)
    }
}

@Composable
private fun ElectricFields(
    profile: BikeProfileEdit,
    enabled: Boolean,
    onChanged: (BikeProfileEdit) -> Unit,
) {
    val assist = profile.electricAssist
    val presence = assist.presence
    Field(presence?.name?.lowercase().orEmpty(), {
        onChanged(profile.copy(electricAssist = assist.copy(presence = it.toPresence())))
    }, stringResource(R.string.bike_edit_presence), enabled)
    if (presence != ComponentPresence.ABSENT) {
        ComponentSection(stringResource(R.string.bike_edit_system), summary(assist.systemManufacturer, assist.systemModel)) {
            Field(assist.systemManufacturer.orEmpty(), {
                onChanged(profile.copy(electricAssist = assist.copy(systemManufacturer = it.blankToNull())))
            }, stringResource(R.string.bike_edit_system_manufacturer), enabled)
            Field(assist.systemModel.orEmpty(), {
                onChanged(profile.copy(electricAssist = assist.copy(systemModel = it.blankToNull())))
            }, stringResource(R.string.bike_edit_system_model), enabled)
        }
        val motor = assist.motor ?: ElectricMotor()
        ComponentSection(stringResource(R.string.bike_edit_motor), summary(motor.position)) {
            Field(motor.position.orEmpty(), {
                onChanged(profile.copy(electricAssist = assist.copy(motor = motor.copy(position = it.blankToNull()))))
            }, stringResource(R.string.bike_edit_motor_position), enabled)
        }
        val battery = assist.battery ?: ElectricBattery()
        ComponentSection(stringResource(R.string.bike_edit_battery), summary(battery.model)) {
            Field(battery.model.orEmpty(), {
                onChanged(profile.copy(electricAssist = assist.copy(battery = battery.copy(model = it.blankToNull()))))
            }, stringResource(R.string.bike_edit_battery_model), enabled)
        }
    }
}

@Composable
private fun PresenceFields(
    identity: ComponentIdentity,
    enabled: Boolean,
    onChanged: (ComponentIdentity) -> Unit,
) {
    Field(identity.presence?.name?.lowercase().orEmpty(), {
        onChanged(identity.copy(presence = it.toPresence()))
    }, stringResource(R.string.bike_edit_presence), enabled)
    if (identity.presence != ComponentPresence.ABSENT) {
        Field(identity.manufacturer.orEmpty(), {
            onChanged(identity.copy(manufacturer = it.blankToNull()))
        }, stringResource(R.string.bike_edit_manufacturer), enabled)
        Field(
            identity.model.orEmpty(),
            { onChanged(identity.copy(model = it.blankToNull())) },
            stringResource(R.string.bike_edit_model),
            enabled,
        )
    }
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
