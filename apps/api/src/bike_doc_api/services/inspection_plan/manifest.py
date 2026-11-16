"""Immutable app-owned inspection definitions. Never revise a published version."""

from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Applicability(StrEnum):
    ALL = "all"
    SUPPORTED_DRIVETRAIN = "supported_drivetrain"
    FRONT_BRAKE = "brakes.front.presence"
    REAR_BRAKE = "brakes.rear.presence"
    FRONT_SUSPENSION = "suspension.fork.type"
    REAR_SUSPENSION = "suspension.rear_shock.presence"
    ELECTRIC_ASSIST = "electric_assist.presence"


# Conditional rules use authoritative resolved-field state; disputed or any
# value outside the two listed cases is unresolved, never silently excluded.
APPLICABILITY_VALUES = MappingProxyType(
    {
        Applicability.FRONT_BRAKE: ("present", "absent"),
        Applicability.REAR_BRAKE: ("present", "absent"),
        Applicability.FRONT_SUSPENSION: ("suspension", "rigid"),
        Applicability.REAR_SUSPENSION: ("present", "absent"),
        Applicability.ELECTRIC_ASSIST: ("present", "absent"),
    }
)


class Evidence(StrEnum):
    TARGETED_REPORT = "targeted_guided_user_report"
    VISUAL_REPORT = "targeted_guided_visual_user_report"
    FUNCTIONAL = "guided_functional_check"
    SPECIFIC_REPORT = "specific_user_report"
    PHOTO = "clear_photo"
    MEASUREMENT = "measurement"


class SafetyCode(StrEnum):
    FRAME = "frame_or_fork_damage_suspected"
    BRAKE = "brake_failure_suspected"
    SUSPENSION = "suspension_internal_concern"
    ELECTRIC = "ebike_electrical_concern"
    UNSAFE = "unsafe_riding_condition"


class ProcedureGroup(FrozenModel):
    number: int = Field(gt=0)
    # These statements are the normative action, observation, and safety boundary,
    # not a prompt template. Each check inherits its group's complete procedure.
    procedure: str = Field(min_length=1)
    stops_and_prohibitions: str = Field(min_length=1)
    constraints: str = Field(min_length=1)


class Section(FrozenModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    group: int = Field(gt=0)


class Check(FrozenModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    condition: str = Field(min_length=1)
    applicability: Applicability
    safety_critical: bool
    default_safety_code: SafetyCode | None
    # OR of ANDs; evidence distinctions retain targeted and functional semantics.
    evidence_alternatives: tuple[frozenset[Evidence], ...] = Field(min_length=1)
    supplemental_evidence: frozenset[Evidence] = frozenset()

    @model_validator(mode="after")
    def validate_check(self) -> Self:
        if self.safety_critical != (self.default_safety_code is not None):
            raise ValueError("safety-critical checks require a default safety code")
        if any(not alternative for alternative in self.evidence_alternatives):
            raise ValueError("empty evidence alternative")
        if len(set(self.evidence_alternatives)) != len(self.evidence_alternatives):
            raise ValueError("duplicate evidence alternative")
        if not any(Evidence.PHOTO not in a for a in self.evidence_alternatives):
            raise ValueError("a photo-free evidence alternative is required")
        return self


class Manifest(FrozenModel):
    version: str = Field(pattern=r"^[a-z][a-z0-9_]*\.v[1-9][0-9]*$")
    sections: tuple[Section, ...] = Field(min_length=1)
    groups: tuple[ProcedureGroup, ...] = Field(min_length=1)
    skill_constraint: str = Field(min_length=1)
    checks: tuple[Check, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        sections = {s.id: s for s in self.sections}
        groups = {g.number for g in self.groups}
        if len(sections) != len(self.sections) or len(groups) != len(self.groups):
            raise ValueError("duplicate section or group")
        if len({c.id for c in self.checks}) != len(self.checks):
            raise ValueError("duplicate check")
        if {s.group for s in self.sections} != groups:
            raise ValueError("section and procedure groups must match")
        if len({s.group for s in self.sections}) != len(self.sections):
            raise ValueError("sections must have distinct procedure groups")
        if any(c.section_id not in sections for c in self.checks):
            raise ValueError("unknown check section")
        if {c.section_id for c in self.checks} != set(sections):
            raise ValueError("every section must contain checks")
        order = {section.id: i for i, section in enumerate(self.sections)}
        if [order[c.section_id] for c in self.checks] != sorted(
            order[c.section_id] for c in self.checks
        ):
            raise ValueError("checks must follow section order")
        if [s.group for s in self.sections] != sorted(groups):
            raise ValueError("groups must follow recommended order")
        # Once published, an existing version cannot be reconstructed with altered
        # rules. New versions remain independent definitions in the registry.
        published = globals().get("GENERAL_INSPECTION_V1")
        if (
            published is not None
            and self.version == published.version
            and self != published
        ):
            raise ValueError("published manifest version cannot be changed")
        return self


GROUPS = (
    ProcedureGroup(
        number=1,
        procedure=(
            "Stabilize the bike, walk around it, and inspect the whole "
            "bike, frame, fork, steerer area, joints, and tubes. Report "
            "cracks, dents, bends, corrosion, unusual paint changes, "
            "missing parts, or other obvious damage."
        ),
        stops_and_prohibitions=(
            "Stop on suspected structural damage, significant impact "
            "evidence, or sharp broken parts. Do not flex, probe, scrape, "
            "remove, or disassemble anything."
        ),
        constraints=(
            "Visual procedure only; adapt attention and safety language "
            "for known carbon components."
        ),
    ),
    ProcedureGroup(
        number=2,
        procedure=(
            "Inspect each tire around its accessible circumference for "
            "cuts, bulges, exposed casing, embedded objects, gross wear, "
            "or obvious loss of air. Reposition the unridden bike as "
            "needed. If comfortable, lift one wheel slightly or securely "
            "support the bike, rotate it slowly, let it stop, and gently "
            "check the stopped wheel for gross side play. Report "
            "retention concerns, wobble, rubbing, looseness, and whether "
            "each tire appears to hold air."
        ),
        stops_and_prohibitions=(
            "Stop on a loose or displaced wheel, severe tire damage, "
            "major wobble, jamming, or discomfort stabilizing the bike. "
            "Never touch a moving wheel or tighten retention hardware."
        ),
        constraints=(
            "Do not require lifting when the user cannot safely stabilize "
            "the bike; use safe off-bike repositioning, or record the "
            "unsupported portion as unable to assess. Do not infer exact "
            "pressure without a measurement."
        ),
    ),
    ProcedureGroup(
        number=3,
        procedure=(
            "Inspect visible brake parts for detachment, severe wear, "
            "damage, cable or hose problems, and fluid leakage. Operate "
            "each brake separately while stationary and gently rock the "
            "bike to confirm that it engages and restrains the "
            "corresponding wheel. Adapt the action for a confirmed "
            "coaster brake."
        ),
        stops_and_prohibitions=(
            "Stop on leakage, detached or broken parts, a control "
            "reaching its limit without braking, or failure to restrain "
            "the wheel. Do not ride-test, adjust, tighten, or touch a "
            "rotor after movement."
        ),
        constraints=(
            "Use only the action appropriate to the resolved brake "
            "configuration. An unresolved configuration must be clarified "
            "rather than guessed."
        ),
    ),
    ProcedureGroup(
        number=4,
        procedure=(
            "With both wheels grounded, gently check the steering and "
            "cockpit for knocking, twisting, or obvious movement, using a "
            "brake during rocking only if that brake already held in "
            "group 3. Gently test the saddle and seatpost for obvious "
            "rocking or rotation. Report movement, cracks, damaged "
            "controls, or looseness."
        ),
        stops_and_prohibitions=(
            "Stop on unexpected movement, cracking, a loose control "
            "surface, or discomfort. Do not tighten fasteners or apply "
            "forceful leverage."
        ),
        constraints=(
            "Omit the brake-assisted headset action when brake evidence "
            "makes it unsafe; record any unassessed portion as a "
            "limitation."
        ),
    ),
    ProcedureGroup(
        number=5,
        procedure=(
            "Inspect the chain, belt, sprockets, chainrings, cranks, "
            "derailleurs or gear unit, and guards for rust, damage, "
            "debris, poor retention, or obviously abnormal slack or "
            "tension. Only when the bike is securely supported and the "
            "configuration permits it, slowly rotate the crank by hand "
            "and report binding, skipping, derailment, unusual noise, or "
            "abnormal motion."
        ),
        stops_and_prohibitions=(
            "Stop on sharp or broken parts, jamming, derailment, or "
            "inability to support the bike. Keep fingers, hair, clothing, "
            "and tools away from teeth, wheels, the moving drive medium, "
            "and other pinch points. Do not shift under load, adjust "
            "tension, or disassemble guards."
        ),
        constraints=(
            "Adapt for chain, belt, fixed-gear, coaster-brake, geared, "
            "and enclosed systems. If safe rotation is incompatible or "
            "unavailable, record the operational portion as unable to "
            "assess rather than improvising."
        ),
    ),
    ProcedureGroup(
        number=6,
        procedure=(
            "Inspect applicable stanchions, seals, crowns, shock body, "
            "mounts, and linkages for damage, leakage, or abnormal "
            "position. Only when earlier findings do not make it unsafe, "
            "gently compress and release the suspension from a stable "
            "control point and report support, smooth movement, binding, "
            "noise, leakage, or failure to return."
        ),
        stops_and_prohibitions=(
            "Stop on structural damage, significant leakage, binding, "
            "collapse, or earlier brake, steering, wheel, frame, or fork "
            "evidence that makes compression unsafe. Do not adjust, open, "
            "inflate, deflate, or touch pressurized internals."
        ),
        constraints=(
            "Resolve front and rear presence separately. Do not apply a "
            "generic compression procedure to an incompatible design."
        ),
    ),
    ProcedureGroup(
        number=7,
        procedure=(
            "With power off, visually inspect the installed battery, "
            "mounts, accessible wiring, connectors, display, and motor "
            "area. If no hazard is present, power the system on while "
            "stationary and report whether it starts normally or shows "
            "warnings."
        ),
        stops_and_prohibitions=(
            "Stop immediately for heat, swelling, odor, smoke, leakage, "
            "sparking, exposed conductors, crash damage, or water-ingress "
            "concern. Do not charge, remove or open the battery, touch "
            "damaged areas, clear codes, or ride-test."
        ),
        constraints=(
            "Perform only on confirmed electric-assist bikes. A system "
            "that cannot safely be powered on must not receive a positive "
            "operational result."
        ),
    ),
)

SECTIONS = tuple(
    Section(id=id, title=title, group=group)
    for group, (id, title) in enumerate(
        (
            ("overview", "Overview"),
            ("wheels_tires", "Wheels and tires"),
            ("brakes", "Brakes"),
            ("contact_controls", "Contact and control points"),
            ("drivetrain", "Drivetrain"),
            ("suspension", "Suspension"),
            ("electric_assist", "Electric assist"),
        ),
        1,
    )
)

# Each alternative is a conjunction; alternatives are disjoined.
REPORT_OR_PHOTO = (frozenset({Evidence.TARGETED_REPORT}), frozenset({Evidence.PHOTO}))
VISUAL_OR_PHOTO = (frozenset({Evidence.VISUAL_REPORT}), frozenset({Evidence.PHOTO}))
VISUAL = (frozenset({Evidence.VISUAL_REPORT}),)
FUNCTIONAL_REPORT = (frozenset({Evidence.FUNCTIONAL, Evidence.SPECIFIC_REPORT}),)
VISUAL_FUNCTIONAL = (
    frozenset({Evidence.VISUAL_REPORT, Evidence.FUNCTIONAL}),
    frozenset({Evidence.PHOTO, Evidence.FUNCTIONAL}),
)


def _check(
    id: str,
    section: str,
    condition: str,
    applicability: Applicability,
    code: SafetyCode | None,
    evidence: tuple[frozenset[Evidence], ...],
    supplements: frozenset[Evidence] = frozenset({Evidence.PHOTO}),
) -> Check:
    return Check(
        id=id,
        section_id=section,
        condition=condition,
        applicability=applicability,
        safety_critical=code is not None,
        default_safety_code=code,
        evidence_alternatives=evidence,
        supplemental_evidence=supplements,
    )


A = Applicability
S = SafetyCode
CHECKS = (
    _check(
        "whole_bike_overview",
        "overview",
        "Overall visible configuration and obvious whole-bike concerns",
        A.ALL,
        None,
        REPORT_OR_PHOTO,
    ),
    _check(
        "frame_fork_visible_condition",
        "overview",
        "Visible frame, fork, steerer, and structural condition",
        A.ALL,
        S.FRAME,
        VISUAL_OR_PHOTO,
    ),
    _check(
        "front_tire_condition",
        "wheels_tires",
        "Visible front-tire damage, inflation concern, and gross wear",
        A.ALL,
        S.UNSAFE,
        VISUAL,
        frozenset({Evidence.PHOTO, Evidence.MEASUREMENT}),
    ),
    _check(
        "rear_tire_condition",
        "wheels_tires",
        "Visible rear-tire damage, inflation concern, and gross wear",
        A.ALL,
        S.UNSAFE,
        VISUAL,
        frozenset({Evidence.PHOTO, Evidence.MEASUREMENT}),
    ),
    _check(
        "front_wheel_security_rotation",
        "wheels_tires",
        (
            "Front-wheel retention, gross side play, rotation, and "
            "obvious wobble or rubbing"
        ),
        A.ALL,
        S.UNSAFE,
        FUNCTIONAL_REPORT,
    ),
    _check(
        "rear_wheel_security_rotation",
        "wheels_tires",
        (
            "Rear-wheel retention, gross side play, rotation, and obvious "
            "wobble or rubbing"
        ),
        A.ALL,
        S.UNSAFE,
        FUNCTIONAL_REPORT,
    ),
    _check(
        "front_brake_condition_operation",
        "brakes",
        "Visible front-brake condition and stationary engagement",
        A.FRONT_BRAKE,
        S.BRAKE,
        VISUAL_FUNCTIONAL,
    ),
    _check(
        "rear_brake_condition_operation",
        "brakes",
        "Visible rear-brake condition and stationary engagement",
        A.REAR_BRAKE,
        S.BRAKE,
        VISUAL_FUNCTIONAL,
    ),
    _check(
        "steering_cockpit_security",
        "contact_controls",
        (
            "Obvious headset, steering, handlebar, stem, lever, and "
            "control security concerns"
        ),
        A.ALL,
        S.UNSAFE,
        FUNCTIONAL_REPORT,
    ),
    _check(
        "saddle_seatpost_security",
        "contact_controls",
        "Obvious saddle and seatpost damage or movement",
        A.ALL,
        S.UNSAFE,
        FUNCTIONAL_REPORT,
    ),
    _check(
        "drivetrain_condition_operation",
        "drivetrain",
        (
            "Visible drive-medium and component condition, retention or "
            "tension concern, and stationary operation"
        ),
        A.SUPPORTED_DRIVETRAIN,
        S.UNSAFE,
        VISUAL_FUNCTIONAL,
    ),
    _check(
        "front_suspension_condition_operation",
        "suspension",
        "Visible fork damage, leakage, binding, or failure to support and return",
        A.FRONT_SUSPENSION,
        S.SUSPENSION,
        VISUAL_FUNCTIONAL,
    ),
    _check(
        "rear_suspension_condition_operation",
        "suspension",
        (
            "Visible rear-shock or linkage damage, leakage, binding, or "
            "failure to support and return"
        ),
        A.REAR_SUSPENSION,
        S.SUSPENSION,
        VISUAL_FUNCTIONAL,
    ),
    _check(
        "electric_assist_condition_operation",
        "electric_assist",
        (
            "Visible battery, wiring, motor-system damage or hazard and "
            "stationary power-on warnings"
        ),
        A.ELECTRIC_ASSIST,
        S.ELECTRIC,
        VISUAL_FUNCTIONAL,
    ),
)

GENERAL_INSPECTION_V1 = Manifest(
    version="general_inspection.v1",
    sections=SECTIONS,
    groups=GROUPS,
    checks=CHECKS,
    skill_constraint=(
        "Procedures are for a novice comfortable performing them. "
        "Discomfort or lower skill does not change applicability; the "
        "user may skip or record unable to assess."
    ),
)

# Add versions here; never remove a version while resumable sessions reference it.
MANIFESTS = MappingProxyType({GENERAL_INSPECTION_V1.version: GENERAL_INSPECTION_V1})


def get_manifest(version: str) -> Manifest:
    """Resolve exactly the snapshotted version; unknown versions fail closed."""
    return MANIFESTS[version]
