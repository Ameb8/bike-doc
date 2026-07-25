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
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
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
                EnumDropdown(
                    identity.bikeType,
                    enumOptions(
                        "road", "gravel", "mountain", "hybrid", "commuter", "cargo", "ebike", "bmx", "folding", "recumbent", "other",
                    ),
                    { onChanged(profile.copy(identity = identity.copy(bikeType = it))) },
                    stringResource(R.string.bike_edit_bike_type),
                    enabled,
                )
            }
            ComponentSection(stringResource(R.string.bike_edit_frame), summary(profile.frame.material, profile.frame.sizeLabel)) {
                val frame = profile.frame
                EnumDropdown(
                    frame.material,
                    enumOptions("aluminum", "steel", "carbon", "titanium", "other"),
                    { onChanged(profile.copy(frame = frame.copy(material = it))) },
                    stringResource(R.string.bike_edit_frame_material),
                    enabled,
                )
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
                BrakeFields(profile.brakes.front, false, enabled) { onChanged(profile.copy(brakes = profile.brakes.copy(front = it))) }
            }
            ComponentSection(
                stringResource(R.string.bike_edit_rear_brake),
                summary(profile.brakes.rear.component.manufacturer, profile.brakes.rear.component.model),
            ) {
                BrakeFields(profile.brakes.rear, true, enabled) { onChanged(profile.copy(brakes = profile.brakes.copy(rear = it))) }
            }
        }
        CollapsibleSection(
            title = stringResource(R.string.bike_edit_drivetrain),
            summary = summary(profile.drivetrain.architecture, profile.drivetrain.driveMedium),
        ) {
            val drivetrain = profile.drivetrain
            EnumDropdown(
                drivetrain.architecture,
                enumOptions(
                    "derailleur",
                    "internal_gear_hub",
                    "gearbox",
                    "singlespeed_freewheel",
                    "fixed_gear",
                    "continuously_variable",
                    "other",
                ),
                {
                    onChanged(profile.copy(drivetrain = drivetrain.copy(architecture = it)))
                },
                stringResource(R.string.bike_edit_architecture),
                enabled,
            )
            EnumDropdown(drivetrain.driveMedium, enumOptions("chain", "belt", "shaft", "other"), {
                onChanged(profile.copy(drivetrain = drivetrain.copy(driveMedium = it)))
            }, stringResource(R.string.bike_edit_drive_medium), enabled)
            RoleSection(stringResource(R.string.bike_edit_front_shifter), drivetrain.frontShifter, RoleKind.SHIFTER, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(frontShifter = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_rear_shifter), drivetrain.rearShifter, RoleKind.SHIFTER, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(rearShifter = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_front_derailleur), drivetrain.frontDerailleur, RoleKind.PLAIN, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(frontDerailleur = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_rear_derailleur), drivetrain.rearDerailleur, RoleKind.REAR_DERAILLEUR, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(rearDerailleur = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_crankset), drivetrain.crankset, RoleKind.CRANKSET, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(crankset = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_rear_cluster), drivetrain.rearCluster, RoleKind.REAR_CLUSTER, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(rearCluster = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_chain), drivetrain.chain, RoleKind.PLAIN, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(chain = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_belt), drivetrain.belt, RoleKind.PLAIN, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(belt = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_gear_unit), drivetrain.gearUnit, RoleKind.PLAIN, enabled) {
                onChanged(profile.copy(drivetrain = drivetrain.copy(gearUnit = it)))
            }
            RoleSection(stringResource(R.string.bike_edit_bottom_bracket), drivetrain.bottomBracket, RoleKind.PLAIN, enabled) {
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
    kind: RoleKind,
    enabled: Boolean,
    onChanged: (DrivetrainRole) -> Unit,
) {
    val value = role ?: DrivetrainRole()
    ComponentSection(label, summary(value.component.manufacturer, value.component.model, value.speedCount?.toString())) {
        RoleFields(value, kind, enabled, onChanged)
    }
}

private enum class RoleKind { PLAIN, SHIFTER, REAR_DERAILLEUR, CRANKSET, REAR_CLUSTER }

@Composable
private fun BrakeFields(
    brake: BrakeAssembly,
    isRear: Boolean,
    enabled: Boolean,
    onChanged: (BrakeAssembly) -> Unit,
) {
    PresenceFields(brake.component, enabled) { onChanged(brake.copy(component = it)) }
    if (brake.component.presence != ComponentPresence.ABSENT) {
        val mechanismValues =
            listOf("disc", "rim_caliper", "rim_cantilever", "rim_v_brake", "rim_u_brake", "rim_other", "drum", "roller") +
                (if (isRear) listOf("coaster") else emptyList()) +
                listOf("other")
        EnumDropdown(brake.mechanism, enumOptions(mechanismValues), {
            onChanged(brake.copy(mechanism = it))
        }, stringResource(R.string.bike_edit_mechanism), enabled)
        EnumDropdown(brake.actuation, enumOptions("mechanical", "hydraulic", "electronic", "other"), {
            onChanged(brake.copy(actuation = it))
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
    kind: RoleKind,
    enabled: Boolean,
    onChanged: (DrivetrainRole) -> Unit,
) {
    PresenceFields(value.component, enabled) { onChanged(value.copy(component = it)) }
    if (value.component.presence != ComponentPresence.ABSENT) {
        if (kind == RoleKind.SHIFTER) {
            EnumDropdown(value.actuation, enumOptions("mechanical", "electronic", "hydraulic", "other"), {
                onChanged(value.copy(actuation = it))
            }, stringResource(R.string.bike_edit_actuation), enabled)
            IntField(value.speedCount, { onChanged(value.copy(speedCount = it)) }, stringResource(R.string.bike_edit_speed_count), enabled)
        }
        if (kind == RoleKind.REAR_DERAILLEUR) {
            EnumDropdown(value.mountType, enumOptions("hanger", "direct_mount", "full_mount", "other"), {
                onChanged(value.copy(mountType = it))
            }, stringResource(R.string.bike_edit_mount_type), enabled)
        }
        if (kind == RoleKind.CRANKSET) {
            IntField(
                value.chainringCount,
                { onChanged(value.copy(chainringCount = it)) },
                stringResource(R.string.bike_edit_chainring_count),
                enabled,
            )
            Field(value.chainringToothCounts.orEmpty(), {
                onChanged(value.copy(chainringToothCounts = it.blankToNull()))
            }, stringResource(R.string.bike_edit_chainring_teeth), enabled)
        }
        if (kind == RoleKind.REAR_CLUSTER) {
            EnumDropdown(value.clusterType, enumOptions("cassette", "freewheel", "single_sprocket", "belt_cog", "other"), {
                onChanged(value.copy(clusterType = it))
            }, stringResource(R.string.bike_edit_cluster_type), enabled)
            EnumDropdown(
                value.driverInterface,
                enumOptions(
                    "hg",
                    "microspline",
                    "xd",
                    "xdr",
                    "campagnolo",
                    "threaded_freewheel",
                    "other",
                ),
                {
                    onChanged(value.copy(driverInterface = it))
                },
                stringResource(R.string.bike_edit_driver_interface),
                enabled,
            )
            IntField(value.speedCount, { onChanged(value.copy(speedCount = it)) }, stringResource(R.string.bike_edit_speed_count), enabled)
        }
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
        EnumDropdown(tire.setup, enumOptions("tubed", "tubeless", "tubular", "airless", "other"), {
            onChanged(position.copy(tire = tire.copy(setup = it)))
        }, stringResource(R.string.bike_edit_tire_setup), enabled)
        val hub = position.hub ?: HubComponent()
        EnumDropdown(hub.axleType, enumOptions("quick_release", "thru_axle", "bolt_on", "solid_axle", "other"), {
            onChanged(position.copy(hub = hub.copy(axleType = it)))
        }, stringResource(R.string.bike_edit_hub_axle_type), enabled)
        if (isRear) {
            EnumDropdown(hub.driverInterface, enumOptions("hg", "microspline", "xd", "xdr", "campagnolo", "threaded_freewheel", "other"), {
                onChanged(position.copy(hub = hub.copy(driverInterface = it)))
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
        EnumDropdown(fork.type, enumOptions("rigid", "suspension", "other"), {
            onChanged(profile.copy(suspension = suspension.copy(fork = fork.copy(type = it))))
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
        EnumDropdown(bar.style, enumOptions("drop", "flat", "riser", "swept", "bullhorn", "bmx", "other"), {
            onChanged(profile.copy(cockpit = cockpit.copy(handlebar = bar.copy(style = it))))
        }, stringResource(R.string.bike_edit_handlebar_style), enabled)
        Field(bar.manufacturer.orEmpty(), {
            onChanged(profile.copy(cockpit = cockpit.copy(handlebar = bar.copy(manufacturer = it.blankToNull()))))
        }, stringResource(R.string.bike_edit_handlebar_manufacturer), enabled)
    }
    val stem = cockpit.stem ?: Stem()
    ComponentSection(stringResource(R.string.bike_edit_stem), summary(stem.type)) {
        EnumDropdown(stem.type, enumOptions("threadless", "quill", "integrated", "other"), {
            onChanged(profile.copy(cockpit = cockpit.copy(stem = stem.copy(type = it))))
        }, stringResource(R.string.bike_edit_stem_type), enabled)
    }
    val seatpost = profile.seating.seatpost ?: Seatpost()
    ComponentSection(stringResource(R.string.bike_edit_seatpost), summary(seatpost.type)) {
        EnumDropdown(seatpost.type, enumOptions("rigid", "dropper", "suspension", "other"), {
            onChanged(profile.copy(seating = profile.seating.copy(seatpost = seatpost.copy(type = it))))
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
    PresenceDropdown(presence, {
        onChanged(profile.copy(electricAssist = assist.copy(presence = it)))
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
            EnumDropdown(motor.position, enumOptions("front_hub", "rear_hub", "mid_drive", "other"), {
                onChanged(profile.copy(electricAssist = assist.copy(motor = motor.copy(position = it))))
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
    PresenceDropdown(identity.presence, { onChanged(identity.copy(presence = it)) }, stringResource(R.string.bike_edit_presence), enabled)
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

private data class EnumOption(val value: String, val label: String)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EnumDropdown(
    value: String?,
    options: List<EnumOption>,
    onValueChange: (String?) -> Unit,
    label: String,
    enabled: Boolean,
) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { if (enabled) expanded = !expanded }) {
        OutlinedTextField(
            value = options.firstOrNull { it.value == value }?.label ?: stringResource(R.string.bike_edit_option_unknown),
            onValueChange = {},
            modifier = Modifier.fillMaxWidth().menuAnchor(),
            readOnly = true,
            enabled = enabled,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(
                text = { Text(stringResource(R.string.bike_edit_option_unknown)) },
                onClick = {
                    expanded = false
                    onValueChange(null)
                },
            )
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option.label) },
                    onClick = {
                        expanded = false
                        onValueChange(option.value)
                    },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PresenceDropdown(
    value: ComponentPresence?,
    onValueChange: (ComponentPresence) -> Unit,
    label: String,
    enabled: Boolean,
) {
    val options =
        listOf(
            ComponentPresence.UNKNOWN to stringResource(R.string.bike_edit_option_unknown),
            ComponentPresence.PRESENT to stringResource(R.string.bike_edit_option_present),
            ComponentPresence.ABSENT to stringResource(R.string.bike_edit_option_absent),
        )
    var expanded by rememberSaveable { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { if (enabled) expanded = !expanded }) {
        OutlinedTextField(
            value = options.firstOrNull { it.first == value }?.second ?: stringResource(R.string.bike_edit_option_unknown),
            onValueChange = {},
            modifier = Modifier.fillMaxWidth().menuAnchor(),
            readOnly = true,
            enabled = enabled,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { (presence, text) ->
                DropdownMenuItem(text = { Text(text) }, onClick = {
                    expanded = false
                    onValueChange(presence)
                })
            }
        }
    }
}

@Composable
private fun enumOptions(vararg values: String): List<EnumOption> = values.map { EnumOption(it, it.toDisplayLabel()) }

@Composable
private fun enumOptions(values: List<String>): List<EnumOption> = values.map { EnumOption(it, it.toDisplayLabel()) }

@Composable
private fun String.toDisplayLabel(): String =
    replace('_', ' ').split(' ').joinToString(" ") { word -> word.replaceFirstChar { it.uppercase() } }

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
