# Bike Doc Automatic Bike Profile Inference Spec

Status: Canonical v1.0
Last updated: 2026-07-10

This document is the canonical product and backend behavior specification for
automatically enriching a known bike profile from user-submitted bicycle
images. Within this scope, it supersedes the automatic profile-enrichment
behavior described in `BIKE_PROFILE_AND_OBSERVATION_REQUESTS_SPEC.md`.

`docs/specs/openapi.yaml` remains the canonical public HTTP contract. The
expanded profile shape required by this feature must be added to that contract
before it is exposed publicly. This document is authoritative for inference,
claim, resolution, and profile-mutation behavior.

## References

- Product design: `docs/specs/bike-doc.md`
- Public API contract: `docs/specs/openapi.yaml`
- Backend organization: `docs/specs/apps/api.md`
- Diagnostic artifact behavior:
  `docs/specs/apps/api-artifacts-diagnostic.md`
- Diagnostic ADK tools: `docs/specs/apps/adk-diagnostic-tools.md`
- Diagnostic safety rules: `docs/specs/apps/safety-diagnostic.md`
- Diagnostic database model: `docs/specs/apps/api-db-diagnostic.md`

## Normative Language

The terms **must**, **must not**, **should**, **should not**, and **may** are
normative. “Must” and “must not” define required behavior. “Should” and
“should not” define the expected default unless a later canonical spec records
a justified exception.

## 1. Purpose

BikeDoc should learn as much durable configuration information as it safely can
from photos the user already submits during normal use. A user should be able to
start with an almost empty technical profile and receive increasingly useful
diagnostic and planning context without completing a separate setup workflow.

The system must balance two goals:

1. Maximize useful bike and bicycle-component context gathered without extra
   user effort.
2. Protect the current profile from unsupported, incorrectly scoped, stale, or
   low-confidence model guesses.

The system achieves that balance by keeping one user-visible bike profile as
the current resolved state while retaining evidence-backed bike fact claims
that explain, support, or dispute each current value.

## 2. Scope

This spec covers:

- automatic inference from user-submitted bicycle images
- the isolated structured model call used for image extraction
- the expanded bike profile schema needed to represent component-level facts
- evidence, provenance, confidence, and abstention requirements
- durable bike fact claims from image inference and manual profile edits
- deterministic backend rules for automatically filling and overwriting fields
- conflict, correction, concurrency, idempotency, and retry behavior
- compact profile certainty and conflict context exposed to phase agents
- backend tests, model evaluations, rollout gates, and production monitoring

This spec assumes the image is already associated with a known, owned bike,
normally through the image's repair session.

## 3. Non-Goals

This spec does not define:

- automatic selection, matching, creation, or merging of bike profiles
- observation requests or profile-information requests sent to the user
- diagnostic conclusions, component condition, wear, damage, or repair state
- planning, part compatibility, or repair-instruction behavior
- an exact make, model, or model-year recognition guarantee
- public UI for reviewing claim history or inference runs
- a mandatory user-confirmation step for each inferred value
- inference from text-only turns, reports, repair history, or external lookups
- a particular model name, provider SDK, prompt body, or deployment target
- final SQL DDL or public OpenAPI migration details

Condition observations such as “chain appears worn,” “rotor may be bent,” or
“frame may be cracked” are diagnostic evidence, not bike profile facts, and
must not be persisted through this feature.

## 4. Canonical Language

### Bike Profile

The **bike profile** is the current resolved, user-visible description of one
bike and its installed configuration. It is a projection of accepted bike fact
claims, not a record of every observation ever made.

### Bike Fact Claim

A **bike fact claim** is an evidence-backed assertion about one canonical
profile field. A claim can be correct, incorrect, stale, corroborating,
conflicting, rejected, or superseded.

Claims from manual edits and model inference use the same claim model. BikeDoc
must not maintain separate “user profile” and “AI profile” sources of truth.

### Field Resolution

A **field resolution** records the current value and epistemic state of one
canonical field path, including the current claim, supporting claims, known
conflicts, source type, effective confidence, and observation time.

### Profile Resolver

The **profile resolver** is deterministic backend behavior that evaluates new
claims against field policy and the latest field resolution. The resolver is
the only behavior allowed to automatically mutate the bike profile.

### Profile Inference Run

A **profile inference run** is one idempotent, versioned attempt to extract bike
fact claims from the newly submitted images in one accepted user action.

### Evidence Cue

An **evidence cue** is a short factual description of what is visible, counted,
or readable in an image. It is audit information, not hidden model reasoning and
not independent proof that a claim is correct.

The canonical language deliberately avoids treating “user-entered” as
synonymous with true and avoids treating “model-inferred” as synonymous with a
mere suggestion. Source, evidence, confidence, scope, recency, and field policy
are evaluated separately.

## 5. Guiding Decisions

### 5.1 One Current Profile, One Unified Claim History

The bike profile must contain the current resolved value for each field. A
unified claim history must preserve how those values were obtained and what
conflicting evidence exists.

The profile must not use blind last-write-wins behavior. The system must not
store a second inferred profile that every consumer has to merge with the
user-entered profile.

### 5.2 Automatic Application Is Expected

An inferred claim that satisfies its field's automatic-application policy must
update the bike profile without asking the user to approve it first.

The app may later show subtle provenance such as “Auto-detected from photo,”
but automatic inference must not create a confirmation inbox or interrupt the
diagnostic flow by default.

### 5.3 Source Is Not Certainty

A manual edit is a high-authority statement about the bike's current
configuration, but it can be mistaken or become stale after a component change.
An image inference can be strongly supported or weakly supported. Resolution
must therefore consider more than source type.

### 5.4 Scope Must Be Explicit

Claims must identify the precise subject they describe. A rear-caliper image
can support `brakes.rear.mechanism`; it cannot silently set the front brake or a
whole-bike aggregate.

### 5.5 The Model Extracts; the Backend Decides

The model produces structured claims and abstentions. Backend code validates
the output, applies field policy, detects conflicts, and performs mutations.
Confidence thresholds, ownership, safety, persistence, and overwrite rules
must not exist only in a prompt.

### 5.6 Inference Must Prefer Precision and Abstention

Automatic profile mutation is a high-precision use case. The extractor should
omit or explicitly abstain on fields it cannot support instead of maximizing
the number of guesses returned for every image.

### 5.7 Profile Confidence Does Not Authorize Risky Guidance

An automatically populated field may improve agent context, but it does not
independently prove that safety-critical repair instructions or exact part
compatibility are appropriate. The relevant safety and information-needs rules
remain independently enforced.

## 6. Expanded Bike Profile Schema

### 6.1 Schema Version and Shape

The current flat bike profile is insufficient for automatic image inference.
In particular:

- one `brake_type` cannot represent different front and rear brakes
- one `drivetrain` string cannot represent the installed drivetrain parts
- one `wheel_size` or `tire_size` cannot represent mixed front and rear sizes
- nullable strings cannot distinguish an unknown component from a component
  that is known to be absent

The expanded schema is named `bike_profile.v2`. It must represent current
values separately from per-field resolution metadata.

All technical fields are optional. A bike may have no resolved technical facts
when it is created. Empty strings are invalid technical values. A `null` leaf
means no current resolved value. Component absence is represented explicitly
with `presence: absent`, not with `null`.

The top-level shape is:

```json
{
  "schema_version": "bike_profile.v2",
  "id": "bike_123",
  "user_id": "usr_123",
  "display_name": "Commuter",
  "has_repair_sessions": true,
  "profile_revision": 7,
  "identity": {},
  "frame": {},
  "brakes": {
    "front": {},
    "rear": {}
  },
  "drivetrain": {},
  "rolling_system": {
    "front": {},
    "rear": {}
  },
  "suspension": {},
  "cockpit": {},
  "seating": {},
  "electric_assist": {},
  "notes": null,
  "created_at": "2026-07-10T12:00:00Z",
  "updated_at": "2026-07-10T12:05:00Z"
}
```

`display_name` and `notes` remain user-managed presentation fields. Image
inference must never populate or overwrite them.

### 6.2 Reusable Component Semantics

Positioned component records use these common fields where applicable:

| Field | Type | Meaning |
|---|---|---|
| `presence` | `unknown`, `present`, `absent` | Whether the component is installed. |
| `manufacturer` | string or null | Normalized component manufacturer. |
| `model` | string or null | Exact or normalized model designation. |

Rules:

- `presence: absent` requires all identity and specification fields for that
  component to be null.
- `presence: present` does not imply that manufacturer or model is known.
- Exact manufacturer or model values are automatically applicable only when
  supported by readable branding, markings, or another field-specific direct
  evidence rule.
- Visual similarity to a product family may be stored as a pending claim but
  must not automatically set an exact model.

### 6.3 Identity and Frame

| Canonical field path | Type | Notes |
|---|---|---|
| `identity.make` | string or null | Bike/frame manufacturer. |
| `identity.model` | string or null | Product model or family. |
| `identity.model_year` | integer or null | 1880 through 2100. |
| `identity.bike_type` | enum or null | `road`, `gravel`, `mountain`, `hybrid`, `commuter`, `cargo`, `ebike`, `bmx`, `folding`, `recumbent`, `other`. |
| `frame.material` | enum or null | `aluminum`, `steel`, `carbon`, `titanium`, `other`. |
| `frame.size_label` | string or null | Printed size such as `M`, `54 cm`, or `17.5 in`. |
| `frame.primary_color` | string or null | Normalized dominant frame color. |
| `frame.secondary_color` | string or null | Optional secondary frame color. |

Automatic identity rules:

- Exact make, model, model year, frame material, or frame size should be
  automatically applied from readable markings.
- Visual classification may auto-fill an empty `identity.bike_type` or frame
  color when the relevant field policy is enabled.
- Visual appearance alone must not automatically set exact make, model, model
  year, frame material, or frame size.
- Image inference must not extract or persist a frame serial number, owner
  identity, location, or other personal information.

### 6.4 Front and Rear Brakes

`brakes.front` and `brakes.rear` are independent brake assemblies. Assembly
fields describe the braking system at that wheel:

| Field | Type | Notes |
|---|---|---|
| `presence` | presence enum | Normally `present`; explicit absence is allowed. |
| `mechanism` | enum or null | `disc`, `rim_caliper`, `rim_cantilever`, `rim_v_brake`, `rim_u_brake`, `rim_other`, `coaster`, `drum`, `roller`, `other`. |
| `actuation` | enum or null | `mechanical`, `hydraulic`, `electronic`, `none`, `other`. |

Each assembly may contain separately identified installed roles:

| Component path | Required role-specific fields beyond common identity |
|---|---|
| `control` | Brake lever or other rider control; common component fields. |
| `brake_unit` | `mount_standard` and `pad_family` plus common fields; represents the caliper or other braking unit. |
| `rotor` | `diameter_mm` plus common fields; disc systems only. |

`brake_unit.mount_standard` supports `flat_mount`, `post_mount`,
`international_standard`, `center_bolt`, `frame_boss`, and `other`.

Brake invariants:

- A single image of one brake end must not populate the other end.
- `mechanism: coaster` is valid only for `brakes.rear`.
- `rotor.presence: present` is valid only when `mechanism: disc`; non-disc
  systems must not retain rotor specifications.
- `rotor.diameter_mm` must not be automatically applied from apparent pixel
  size alone. A readable marking or a field-policy-approved calibrated
  observation is required.
- `mechanism: disc` plus `actuation: hydraulic` replaces the old aggregate
  meaning `hydraulic_disc` for that position.
- Whole-bike brake summaries are derived compatibility values. The model must
  not emit a whole-bike `brake_type` claim.

### 6.5 Structured Drivetrain

The V2 drivetrain replaces the free-text `drivetrain` field with system-level
facts and installed component roles.

System-level fields:

| Canonical field path | Type | Notes |
|---|---|---|
| `drivetrain.architecture` | enum or null | `derailleur`, `internal_gear_hub`, `gearbox`, `singlespeed_freewheel`, `fixed_gear`, `continuously_variable`, `other`. |
| `drivetrain.drive_medium` | enum or null | `chain`, `belt`, `shaft`, `other`. |
| `drivetrain.front_chainring_count` | integer or null | Derived from the resolved crankset when possible. |
| `drivetrain.rear_speed_count` | integer or null | Derived from rear cluster, gear unit, or shifter facts when they agree. |

Installed drivetrain roles:

| Component path | Required role-specific fields beyond common identity |
|---|---|
| `drivetrain.front_shifter` | `actuation`, `speed_count` |
| `drivetrain.rear_shifter` | `actuation`, `speed_count` |
| `drivetrain.front_derailleur` | common component fields |
| `drivetrain.rear_derailleur` | `mount_type` plus common fields |
| `drivetrain.crankset` | `chainring_count`, `chainring_tooth_counts` |
| `drivetrain.rear_cluster` | `cluster_type`, `speed_count`, `smallest_sprocket_teeth`, `largest_sprocket_teeth`, `driver_interface` |
| `drivetrain.chain` | `speed_compatibility` plus common fields |
| `drivetrain.belt` | common component fields |
| `drivetrain.gear_unit` | `speed_count` plus common fields; used for an internal hub, gearbox, or continuously variable unit |
| `drivetrain.bottom_bracket` | `interface`, `shell_width_mm` plus common fields |

Role-specific enums include:

- shifter `actuation`: `mechanical`, `electronic`, `hydraulic`, `other`
- rear derailleur `mount_type`: `hanger`, `direct_mount`, `full_mount`, `other`
- rear cluster `cluster_type`: `cassette`, `freewheel`, `single_sprocket`,
  `belt_cog`, `other`
- rear cluster `driver_interface`: `hg`, `microspline`, `xd`, `xdr`,
  `campagnolo`, `threaded_freewheel`, `other`

Drivetrain invariants:

- Known absence must be explicit. A resolved 1x drivetrain can have
  `front_derailleur.presence: absent`; null does not mean absent.
- Exact sprocket, chainring, or speed counts require a clear counted view,
  readable marking, or agreement between independently supported component
  facts.
- The resolver may derive system-level counts. The model should prefer claims
  about the directly observed component over redundant aggregate claims.
- A component logo can support its manufacturer. An exact model requires
  readable model markings or another field-policy-approved direct cue.
- A free-text drivetrain summary may be derived for display, but it is not a
  canonical inference target.

### 6.6 Front and Rear Rolling Systems

`rolling_system.front` and `rolling_system.rear` are independent. This permits
mixed wheel or tire sizes and different hub interfaces.

Each position may contain:

#### Wheel and Rim

| Field | Type | Notes |
|---|---|---|
| `wheel.presence` | presence enum | Wheel installation state. |
| `wheel.manufacturer` | string or null | Complete wheel manufacturer when known. |
| `wheel.model` | string or null | Complete wheel model when known. |
| `wheel.nominal_size` | string or null | Common designation such as `700c`, `650b`, `29 in`, or `26 in`. |
| `wheel.iso_bsd_mm` | integer or null | ISO bead-seat diameter. |
| `rim.manufacturer` | string or null | Rim manufacturer. |
| `rim.model` | string or null | Rim model. |
| `rim.internal_width_mm` | number or null | Exact internal rim width. |

#### Tire

| Field | Type | Notes |
|---|---|---|
| `tire.presence` | presence enum | Tire installation state. |
| `tire.manufacturer` | string or null | Tire manufacturer. |
| `tire.model` | string or null | Tire model. |
| `tire.marked_size` | string or null | Normalized rendering of readable sidewall size. |
| `tire.iso_width_mm` | integer or null | ISO nominal width. |
| `tire.iso_bsd_mm` | integer or null | ISO bead-seat diameter. |
| `tire.setup` | enum or null | `tubed`, `tubeless`, `tubular`, `airless`, `other`. |
| `tire.tubeless_ready` | boolean or null | Product capability, not proof of current setup. |

#### Hub and Wheel Retention

| Field | Type | Notes |
|---|---|---|
| `hub.manufacturer` | string or null | Hub manufacturer. |
| `hub.model` | string or null | Hub model. |
| `hub.axle_type` | enum or null | `quick_release`, `thru_axle`, `bolt_on`, `solid_axle`, `other`. |
| `hub.axle_standard` | string or null | Normalized designation such as `12x142`. |
| `hub.rotor_mount` | enum or null | `six_bolt`, `centerlock`, `none`, `other`. |
| `hub.driver_interface` | driver-interface enum or null | Rear only; null for the front. |

Rolling-system invariants:

- A tire sidewall marking can support tire size, manufacturer, and model but
  must not automatically prove rim size or the opposite wheel's tire size.
- Exact dimensions must come from readable markings, user measurements covered
  by another spec, or a field-policy-approved direct observation. Apparent
  image scale alone is insufficient.
- `tire.tubeless_ready: true` must not imply `tire.setup: tubeless`.
- Rear hub driver interface must not be inferred only from drivetrain brand.

### 6.7 Suspension

| Canonical field path | Type | Notes |
|---|---|---|
| `suspension.fork.type` | enum or null | `rigid`, `suspension`, `other`. |
| `suspension.fork.manufacturer` | string or null | Fork manufacturer. |
| `suspension.fork.model` | string or null | Fork model. |
| `suspension.fork.travel_mm` | integer or null | Marked or otherwise directly supported travel. |
| `suspension.rear_shock.presence` | presence enum | Explicitly distinguishes hardtail/rigid rear from unknown. |
| `suspension.rear_shock.manufacturer` | string or null | Rear shock manufacturer. |
| `suspension.rear_shock.model` | string or null | Rear shock model. |
| `suspension.rear_travel_mm` | integer or null | Bike rear-wheel travel when directly supported. |

The presence of a suspension fork or rear shock may be inferred directly from
a clear whole-bike or component image. Exact travel must not be estimated from
appearance alone.

### 6.8 Cockpit and Seating

These fields capture common diagnostic and replacement interfaces that are
often visible or printed on installed components.

| Canonical field path | Type | Notes |
|---|---|---|
| `cockpit.handlebar.style` | enum or null | `drop`, `flat`, `riser`, `swept`, `bullhorn`, `bmx`, `other`. |
| `cockpit.handlebar.manufacturer` | string or null | Handlebar manufacturer. |
| `cockpit.handlebar.model` | string or null | Handlebar model. |
| `cockpit.stem.type` | enum or null | `threadless`, `quill`, `integrated`, `other`. |
| `cockpit.stem.manufacturer` | string or null | Stem manufacturer. |
| `cockpit.stem.model` | string or null | Stem model. |
| `cockpit.headset.type` | enum or null | `external_cup`, `zero_stack`, `integrated`, `threaded`, `other`. |
| `seating.seatpost.presence` | presence enum | Seatpost installation state. |
| `seating.seatpost.type` | enum or null | `rigid`, `dropper`, `suspension`, `other`. |
| `seating.seatpost.manufacturer` | string or null | Seatpost manufacturer. |
| `seating.seatpost.model` | string or null | Seatpost model. |
| `seating.seatpost.diameter_mm` | number or null | Exact marked diameter. |

Exact headset standards, clamp sizes, and seatpost diameter must not be
estimated solely from visual scale.

### 6.9 Electric Assist

| Canonical field path | Type | Notes |
|---|---|---|
| `electric_assist.presence` | presence enum | Whether electric assist is installed. |
| `electric_assist.system_manufacturer` | string or null | System-level manufacturer. |
| `electric_assist.system_model` | string or null | System-level model/family. |
| `electric_assist.motor.position` | enum or null | `front_hub`, `rear_hub`, `mid_drive`, `other`. |
| `electric_assist.motor.manufacturer` | string or null | Motor manufacturer. |
| `electric_assist.motor.model` | string or null | Motor model. |
| `electric_assist.battery.manufacturer` | string or null | Battery manufacturer. |
| `electric_assist.battery.model` | string or null | Battery model. |
| `electric_assist.battery.nominal_voltage_v` | number or null | Readable marked voltage. |

Electric-assist facts may improve context but must not weaken the existing rule
that electrical, battery, or motor safety concerns may require specialist
service.

### 6.10 Field Resolution Metadata

Every resolved technical leaf field must have a corresponding field-resolution
record. The storage implementation may use a normalized table or a validated
JSONB map, but it must preserve this semantic shape:

```json
{
  "field_path": "brakes.rear.actuation",
  "resolution_state": "resolved",
  "current_claim_id": "bfc_123",
  "supporting_claim_ids": ["bfc_124"],
  "conflicting_claim_ids": [],
  "effective_confidence": "high",
  "source_type": "image_inference",
  "observed_at": "2026-07-10T12:00:00Z",
  "resolved_at": "2026-07-10T12:00:05Z"
}
```

Allowed `resolution_state` values are:

| State | Meaning |
|---|---|
| `unknown` | No usable current claim exists. |
| `resolved` | Policy selected a current claim. |
| `disputed` | A current claim exists, but one or more strong conflicting claims require caution. |
| `cleared` | A user explicitly cleared the field; older claims cannot refill it. |

`effective_confidence` uses `unknown`, `low`, `medium`, or `high` for compact
agent context. It is a resolver output, not a copy of the model's raw score.

### 6.11 Versioned Field Registry

The backend must maintain a versioned registry of canonical field paths. Each
entry defines:

- value type and validation rules
- allowed enum values where applicable
- component scope and position rules
- volatility class
- consequence class
- permitted evidence bases
- whether image inference may auto-fill an empty field
- which existing source types image inference may auto-supersede
- whether readable markings or counted evidence are required
- any derived-field or aggregation rule
- the assigned field-policy bundle or stricter field-specific override
- the calibration key and active policy version used to map evidence attributes
  and model score to effective confidence

The model must not invent arbitrary field paths. Unknown paths, invalid values,
or invalid field/scope combinations must be rejected before claim persistence.

## 7. Legacy Profile Compatibility

The V1 aggregate fields are deprecated inference targets:

- `brake_type`
- `drivetrain`
- `wheel_size`
- `tire_size`

Migration behavior must preserve existing user data without pretending that
the old schema carried more scope than it did.

### 7.1 Legacy Data Migration

- `make`, `model`, `model_year`, `bike_type`, and `frame_material` map to their
  V2 paths.
- Legacy `mechanical_disc` and `hydraulic_disc` values create front and rear
  mechanism/actuation claims with `source_type: legacy_profile_migration` and
  an explicit `scope_assumption: whole_bike`. Newer position-specific evidence
  may supersede either end independently.
- Legacy `rim` creates front and rear `mechanism: rim_other` claims with the
  same whole-bike scope assumption; the migration must not invent a rim-brake
  subtype.
- Legacy `coaster` creates only `brakes.rear.mechanism: coaster` and
  `brakes.rear.actuation: none`; the front brake remains unknown.
- Legacy `other` is preserved as a legacy brake summary and does not create
  positioned mechanism or actuation claims.
- Legacy `wheel_size` and `tire_size` similarly create front and rear legacy
  claims with the whole-bike scope assumption.
- Legacy free-text `drivetrain` is retained as
  `drivetrain.legacy_description`. It must not be deterministically parsed into
  structured fields as part of the image-inference migration.
- Legacy `unknown` sentinels map to null V2 values.

`brakes.legacy_summary` and `drivetrain.legacy_description` are migration-only
compatibility metadata. They are not image-inference field paths and must not
be emitted by the extractor.

### 7.2 Legacy Read Fields

During a compatibility period, the backend may return aggregate fields derived
from V2:

- aggregate `brake_type` is returned only when front and rear resolve to the
  same legacy-compatible type
- aggregate `wheel_size` or `tire_size` is returned only when front and rear
  resolve to the same normalized value
- aggregate `drivetrain` is a derived display summary when enough structured
  fields exist; otherwise the legacy description is retained

Mixed configurations must not be collapsed into a misleading aggregate value.

### 7.3 Legacy Writes

If legacy PATCH fields remain supported during migration, a manual aggregate
write must use the same mapping rules as Section 7.1. Symmetric disc, rim,
wheel, and tire values use explicit whole-bike scope assumptions; `coaster`
maps only to the rear; and ambiguous `other` does not invent positioned facts.
V2 clients should write the positioned and structured fields directly.

The OpenAPI and Android profile models must be revised in a coordinated
follow-up before V2 is exposed publicly.

## 8. Evidence and Bike Fact Claims

### 8.1 Unified Claim Model

All durable technical profile changes governed by this spec must be explainable
through a bike fact claim. This includes manual edits and clears of canonical
technical fields, image inference, legacy migration, and backend-derived
values. User-managed `display_name` and `notes` remain outside the bike fact
claim model.

Required claim semantics:

```json
{
  "id": "bfc_123",
  "bike_id": "bike_123",
  "field_path": "brakes.rear.actuation",
  "value": "hydraulic",
  "source_type": "image_inference",
  "source_ref": {
    "type": "profile_inference_run",
    "id": "pir_123"
  },
  "evidence_refs": [
    {"type": "artifact", "id": "art_123"}
  ],
  "observed_at": "2026-07-10T12:00:00Z",
  "evidence_basis": "direct_visual",
  "visibility": "clear",
  "model_score": 0.97,
  "evidence_cues": [
    "A hose enters the installed rear caliper.",
    "No cable-actuation arm is visible."
  ],
  "disposition": "applied",
  "disposition_reason": "auto_fill_policy_satisfied",
  "created_at": "2026-07-10T12:00:05Z"
}
```

Claim values and provenance are immutable after creation. Disposition metadata
may change as later resolution supersedes, rejects, or conflicts with a claim.
Image-specific fields such as `model_score`, `evidence_basis`, `visibility`,
and `evidence_cues` are nullable for non-image claim sources. A manual clear
uses a null value plus `source_type: manual_profile_clear`.

### 8.2 Source Types

This spec defines:

| Source type | Meaning |
|---|---|
| `manual_profile_edit` | User supplied a current value through profile editing. |
| `manual_profile_clear` | User explicitly cleared a field. |
| `image_inference` | Structured value extracted from submitted image evidence. |
| `legacy_profile_migration` | Value migrated from the V1 profile schema. |
| `derived_resolution` | Backend-derived aggregate or summary from resolved component facts. |

Later canonical specs may add sources. They must use the same claim and
resolution model rather than create a parallel profile store.

### 8.3 Claim Dispositions

| Disposition | Meaning |
|---|---|
| `pending` | Valid claim retained but not automatically applicable. |
| `applied` | Claim currently supplies the profile value. |
| `supporting` | Claim agrees with and strengthens the current value. |
| `conflict` | Claim materially disagrees with the current resolution. |
| `superseded` | A newer or stronger claim replaced it as current. |
| `rejected` | Evidence or an explicit correction established that the claim should not be used. |

“Superseded” does not mean a claim was historically wrong. A tire, brake, or
drivetrain part may simply have changed.

### 8.4 Observation Time

Resolution recency must use the time the evidence entered BikeDoc, normally the
accepted turn timestamp, not the time a background model call completed.
Untrusted image EXIF timestamps must not determine claim ordering.

## 9. Profile Inference Model Contract

### 9.1 Execution Shape

The initial implementation should use one isolated structured multimodal model
call rather than a conversational subagent or tool-using ADK loop. An internal
adapter may use the configured model provider, but provider and model details
must remain outside product contracts.

If a future implementation uses ADK internally, it must preserve this same
single-purpose structured interface and must not expose an ADK tool that allows
the phase agent to bypass backend resolution policy.

### 9.2 Model Input

One inference run receives:

- all newly submitted, ready image artifacts in the accepted user action
- the user's caption from that action, when present
- the known server-owned bike and repair-session context identifiers
- the current versioned inference output schema and field registry

The caption may clarify whether an object is installed, loose, a proposed
replacement, packaging, or unrelated. Text in the caption must not independently
create profile facts under this image-only spec.

The inference call must not receive by default:

- existing profile values
- existing claims or conflicts
- the full diagnostic transcript
- prior phase reports
- repair history
- safety decisions or current hypotheses

Withholding existing values reduces confirmation bias. The backend resolver,
not the extractor, compares new claims with current profile state.

### 9.3 Structured Output

The output schema is `bike_profile_inference.v1`:

```json
{
  "schema_version": "bike_profile_inference.v1",
  "scene": {
    "contains_bicycle": true,
    "multiple_bicycles": false,
    "target_relation": "installed_on_target_bike",
    "confidence_score": 0.99
  },
  "claims": [
    {
      "field_path": "brakes.rear.mechanism",
      "value": "disc",
      "subject_relation": "installed_on_target_bike",
      "evidence_basis": "direct_visual",
      "visibility": "clear",
      "confidence_score": 0.99,
      "artifact_ids": ["art_123"],
      "observed_text": null,
      "evidence_cues": [
        "A rotor and caliper are visible at the rear wheel."
      ]
    },
    {
      "field_path": "brakes.rear.actuation",
      "value": "hydraulic",
      "subject_relation": "installed_on_target_bike",
      "evidence_basis": "direct_visual",
      "visibility": "clear",
      "confidence_score": 0.97,
      "artifact_ids": ["art_123"],
      "observed_text": null,
      "evidence_cues": [
        "A hose enters the caliper and no cable arm is visible."
      ]
    }
  ],
  "abstentions": [
    {
      "field_path": "brakes.front.actuation",
      "reason": "front_brake_not_visible"
    }
  ]
}
```

Allowed `target_relation` and `subject_relation` values are:

| Value | Meaning |
|---|---|
| `installed_on_target_bike` | Clearly installed on the known bike. |
| `likely_installed_on_target_bike` | Probably installed, but view or scene is incomplete. |
| `loose_component` | Component is not visibly installed. |
| `packaging_or_reference` | Product packaging, manual, listing, or reference image. |
| `other_bike` | Evidence appears to concern a different bike. |
| `ambiguous` | Relationship cannot be established. |

Only `installed_on_target_bike` claims are eligible for automatic profile
mutation. Other valid claims may be retained as pending evidence but must not
change the current installed configuration.

Allowed `evidence_basis` values are:

| Basis | Meaning |
|---|---|
| `readable_marking` | Text, logo, size, or model marking is readable. |
| `direct_visual` | The part or mechanism is directly visible. |
| `counted_visual` | A count is supported by a sufficiently clear view. |
| `derived_visual` | Value is inferred indirectly from appearance or context. |

Allowed `visibility` values are `clear`, `partial`, and `poor`.

### 9.4 Output Rules

- The model must return normalized values conforming to the field registry.
- It must omit or abstain rather than use `unknown` as a claim value.
- It must not emit arbitrary fields, condition assessments, diagnoses, or
  repair recommendations.
- Each claim must cite at least one artifact from the inference input.
- `observed_text` should contain short exact visible text when readable
  markings support the claim.
- `evidence_cues` must contain no more than three concise factual cues.
- The model must not return chain-of-thought, hidden reasoning, or long
  explanations.
- The raw `confidence_score` is model output and must never be treated as a
  calibrated probability without field-specific evaluation.
- The entire output must pass strict schema validation with extra fields
  forbidden. Schema-invalid output fails the run and creates no claims.

## 10. Processing Lifecycle

### 10.1 Trigger

The initial diagnostic trigger occurs after a user turn containing image
artifact IDs has been accepted and every artifact has passed ownership and
repair-session association checks.

Inference must not start merely because a file upload completed. Upload occurs
before turn submission and does not prove the user actually submitted the
artifact as evidence.

Every newly submitted ready image must be included in exactly one normal
inference run for the accepted action. Multiple images submitted together
should be analyzed in one run so the extractor can combine views and avoid
contradictory single-image guesses.

### 10.2 Non-Blocking Execution

Profile inference runs asynchronously and must not delay or fail the diagnostic
turn. The diagnostic agent receives the submitted images directly for current
turn reasoning. Profile changes produced by the enrichment run are guaranteed
only for subsequent profile reads.

### 10.3 Required Sequence

1. Accept and persist the user turn.
2. Validate that referenced artifacts are owned, ready images associated with
   the known repair session.
3. Create or reuse the idempotent inference run.
4. Load the images and current user caption through app-owned artifact access.
5. Call the structured extractor adapter in isolated context.
6. Strictly validate output against the inference schema and field registry.
7. Persist valid claims and provenance.
8. Lock or revision-check the latest profile and field resolutions.
9. Run deterministic resolution against the latest state.
10. Atomically persist claim dispositions, field resolutions, and profile
    projection changes.
11. Record structured metrics and completion status.

No prompt, model callback, or ADK tool may write profile fields directly.

## 11. Backend Module and Interface

Automatic enrichment should be exposed to its caller through one deep backend
module interface, conceptually:

```text
process_submitted_profile_evidence(turn_id) -> ProfileInferenceOutcome
```

The caller supplies only the server-owned turn ID. The module implementation
owns:

- loading and validating the turn, session, bike, and artifacts
- inference-run idempotency and retries
- minimal model input construction
- extractor adapter invocation
- schema and field-registry validation
- claim persistence
- deterministic resolution
- atomic profile projection updates
- structured observability

Internal seams may exist for a model extractor adapter and persistence fakes.
The external interface must not make callers understand confidence thresholds,
claim dispositions, field precedence, or SQL transactions.

This module belongs behind the backend services layer. It is not a public HTTP
route and is not a phase-agent tool.

## 12. Field Policy and Resolution

### 12.1 Field Classifications

Every field registry entry has a volatility class:

| Class | Examples | Resolution implication |
|---|---|---|
| `stable_identity` | make, model, model year, frame material, frame size | Changes are rare; manual conflicts are not silently overwritten by image inference. |
| `descriptive` | bike type, frame color, handlebar style | Subjective or low consequence; image inference may fill blanks, but manual conflicts normally remain current. |
| `installed_configuration` | brakes, drivetrain parts, tires, wheels, suspension, electric-assist equipment | Parts can change; newer direct installed evidence may supersede older manual or inferred values. |
| `derived` | rear speed count, front chainring count, legacy summaries | Backend-owned; model claims cannot write these directly when a component-level derivation exists. |
| `user_managed` | display name, notes | Never written by image inference. |

Each field also has a consequence class:

| Class | Meaning |
|---|---|
| `low` | An incorrect value is inconvenient but unlikely to affect safety or exact compatibility. |
| `compatibility` | An incorrect value could lead to a wrong replacement part or incompatible procedure. |
| `safety` | An incorrect value could materially affect safe guidance. |

Consequence class affects how agents may use a resolved value; it does not
turn model confidence into safety proof.

### 12.2 Evidence Eligibility

Default evidence requirements are:

| Field kind | Automatic evidence requirement |
|---|---|
| Exact make/model/year | Clear readable marking; model year may also require multiple corroborating readable cues. |
| Exact component manufacturer/model | Clear readable logo/marking; visual product similarity alone is pending only. |
| Brake mechanism/actuation | Clear direct view of the positioned installed brake. |
| Sprocket/chainring/speed count | Clear counted view, readable marking, or agreement between independently supported installed parts. |
| Tire size | Readable sidewall marking. |
| Wheel ISO size | Readable rim/tire marking or a consistent derived resolution; not apparent scale. |
| Exact dimensions | Readable marking or another canonical measured source; not apparent scale. |
| Component presence/absence | Clear view with enough scope to establish the relevant position. |
| Bike type, color, bar style | Clear direct whole-bike or component view. |
| Frame material | Readable marking for automatic application; appearance-only claims remain pending. |

### 12.3 Confidence Calibration

The system must not use one global raw model-score threshold.

For every automatically mutable field/evidence class, evaluation must establish
a mapping from model score and evidence attributes to an effective confidence
and a configured application threshold. Auto-fill and auto-overwrite are
calibrated separately.

In production, if a field lacks sufficient evaluation evidence, image claims
for that field remain pending even if the model reports a high score. Section
12.3.1 defines the limited non-production development exception.

Threshold values are deployment configuration produced from evaluation, not
prompt text and not part of the public contract.

#### 12.3.1 Initial Development Bootstrap Policy

Before comprehensive held-out evaluations are complete, a non-production
development deployment may enable automatic mutation through the versioned
`bootstrap-v1` resolver policy. This exception exists so the profile-enrichment
workflow, claim history, manual barriers, and resolver behavior can be exercised
end to end before production evaluation data exists.

`bootstrap-v1` must:

- remain explicit deployment configuration, never prompt-only behavior;
- be identified in resolver telemetry and inference outcomes as
  `policy_mode: provisional`;
- use separate thresholds by field-policy bundle and evidence type, not one
  global raw-score threshold;
- treat the model's `confidence_score` as a provisional routing signal, not a
  calibrated probability;
- preserve all normal validation, installedness, scope, manual-edit, manual-
  clear, transaction, and conflict rules in this specification; and
- be replaceable field by field by an evaluation-calibrated policy without a
  schema migration or public-contract change.

Production automatic mutation must use a policy whose relevant field/evidence
class has passed the configured held-out precision gate. Until then, the same
claim may be stored as pending evidence in production.

#### 12.3.2 Bootstrap Field-Policy Bundles

The initial registry must assign every image-inference target to one of these
bundles or to a stricter field-specific override. All thresholds below are
inclusive provisional defaults over a claim's `confidence_score` after the
claim has passed schema, field, scope, and evidence validation.

| Bundle | Typical fields | Required evidence | Auto-fill | Auto-overwrite |
|---|---|---|---:|---:|
| `visual_descriptive` | bike type, frame color, handlebar style, clearly visible component presence | `direct_visual`, `clear` | 0.90 | disabled |
| `installed_mechanism` | brake mechanism/actuation, drive medium, axle type, rotor mount, stem/seatpost type | positioned `direct_visual`, `clear`, installed | 0.92 | 0.97, newer direct installed evidence only |
| `readable_identity` | make, manufacturer, exact component model, frame size, marked tire size | `readable_marking`, `clear`, with observed text where applicable | 0.90 | 0.95 for inferred or legacy current claim only; manual stable identity never |
| `counted_spec` | directly observed speed, sprocket, and chainring counts | `counted_visual` or `readable_marking`, `clear`, installed | 0.95 | 0.98, newer qualifying evidence only |
| `exact_dimension` | marked rotor diameter, wheel ISO size, seatpost diameter, travel | `readable_marking`, `clear`, installed where applicable | 0.95 | 0.98, newer readable-marking evidence only |
| `inference_only_pending` | model year without an explicit marking, frame material from appearance, tire setup, exact identity from visual similarity | insufficient for automatic use | disabled | disabled |
| `derived` | aggregate counts and legacy summaries | backend derivation from resolved component facts | disabled | backend recomputation only |
| `user_managed` | display name, notes | n/a | disabled | disabled |

An accepted bundle produces provisional `effective_confidence: medium` at or
above its auto-fill threshold and `high` at or above its auto-overwrite
threshold. A claim below its bundle's auto-fill threshold produces `low` and
remains pending. A field-specific evaluated policy may replace these mappings
and thresholds.

The registry must use the stricter rule when a field could fit multiple
bundles. Exact identity and dimensions must never fall back to an appearance-
only bundle.

### 12.4 Resolution Actions

For each validated claim, the resolver performs exactly one action:

| Situation | Required action |
|---|---|
| Current field is unknown and auto-fill policy passes | Apply claim. |
| Claim agrees with current value | Mark supporting; update supporting evidence and effective confidence as policy allows. |
| Claim conflicts with older image inference and overwrite policy passes | Apply new claim; supersede old current claim. |
| Claim conflicts but overwrite policy does not pass | Mark conflict or pending; retain current value. |
| Claim concerns a loose/replacement/reference component | Keep pending or reject for current-profile use; do not mutate profile. |
| Claim targets the wrong position or has invalid scope | Reject before mutation. |
| Claim targets a derived or user-managed field | Reject direct model mutation. |

The resolver must implement the following decision order for an image claim:

1. Validate and normalize the field path, value, scope, component invariants,
   evidence references, and subject relation.
2. Reject invalid, wrongly positioned, derived, or user-managed claims. Keep a
   valid but non-installed, partial, poor-visibility, or policy-ineligible
   claim pending rather than allowing it to mutate the installed profile.
3. Load the latest field resolution and applicable versioned field policy in
   the same transaction. Apply a manual edit or clear barrier before comparing
   values; evidence observed at or before that barrier must not refill or
   replace the field.
4. Compute effective confidence from the active policy. Under `bootstrap-v1`,
   apply the bundle mapping in Section 12.3.2.
5. If the current field is unknown or eligible to refill after a later manual
   clear, apply only a claim that passes auto-fill. Otherwise retain it as
   pending.
6. If the normalized candidate value equals the current value, mark it
   supporting and update effective confidence only as the active field policy
   permits.
7. If the candidate conflicts, apply it only when the active overwrite policy
   permits replacement of the current source type and the candidate is newer.
   Otherwise retain the selected current value and mark the candidate conflict
   or pending as appropriate.
8. Recompute affected derived fields only from resolved component facts. If
   their supporting facts disagree, leave the derived value null or disputed.
9. Atomically persist the claim disposition, field resolution, derived changes,
   profile projection, and revision.

For this decision order, `pending` means the claim is valid but has not met an
automatic-application rule. `conflict` means it is valid, materially differs
from a selected current value, and did not win under the overwrite policy.
`rejected` is reserved for invalid scope/value/evidence or an explicit user
correction that establishes the claim must not be used.

### 12.5 Manual Edits and Clears

- A valid manual value applies immediately and becomes the current claim.
- A manual edit after an inference run starts must be considered before that
  run resolves; stale async completion must not blindly overwrite it.
- A manual correction may explicitly reject a prior model claim. Without an
  explicit “that was never correct” signal, an older configuration claim is
  normally superseded rather than rejected.
- A manual clear creates a `manual_profile_clear` claim and
  `resolution_state: cleared`. Evidence observed before that clear cannot
  refill the field.
- Newer qualifying evidence may refill a cleared installed-configuration field.
- The same image, content hash, inference run, or older evidence must never
  flip a field back after a later manual edit or clear.

### 12.6 Automatic Overwrite of Manual Values

Manual values are not permanently locked, but automatic overwrite is
field-specific:

- `stable_identity`: image inference must not silently overwrite a conflicting
  manual value. Record the conflict and retain the manual value.
- `descriptive`: image inference normally retains the manual value and records
  a conflict when useful.
- `installed_configuration`: newer, clear, direct evidence that the part is
  installed may supersede an older manual value when the field-specific
  overwrite policy passes.
- `derived`: recompute from the newly resolved component facts.
- `user_managed`: never overwrite.

This allows a clear current photo of an installed hydraulic rear brake to
replace an old `mechanical` rear-brake claim while preventing a weak visual
guess from changing the bike's make or model year.

### 12.7 Conflicts

A material unresolved conflict must:

- retain both claims and provenance
- set `resolution_state: disputed`
- preserve the selected current claim when policy still identifies a winner
- expose compact conflict metadata to phase agents
- prevent the disputed field from satisfying safety-critical or exact
  compatibility requirements on its own

This spec does not define how or when the user is asked to resolve a conflict.

### 12.8 Derived Values

Derived values are computed only from resolved component facts. Examples:

- `drivetrain.rear_speed_count` from an agreed rear cluster, shifter, or gear
  unit count
- `drivetrain.front_chainring_count` from the crankset
- legacy brake summary from matching front and rear brake assemblies
- legacy wheel/tire size from matching front and rear values

If supporting component facts disagree, the derived value must be null or
disputed rather than chosen by arbitrary precedence.

## 13. Concurrency, Transactions, and Idempotency

### 13.1 Inference Run Identity

Normal processing must be idempotent for the tuple:

```text
(turn_id, inference_schema_version, extractor_version)
```

A retry of the same run must return the prior outcome or safely resume without
creating duplicate claims or repeating profile mutations.

An explicit future reprocessing operation may use a new extractor version. It
must create a new versioned run and new claims; normal turn processing must not
silently reprocess all historical photos.

### 13.2 Transactional Resolution

The resolver must evaluate claims against the latest persisted profile, not a
profile snapshot sent to the model.

Claim dispositions, field-resolution changes, profile projection changes, and
the profile revision must commit atomically. Implementations may use row locks,
optimistic revision checks, or both.

If concurrent manual and inferred writes race, the resolver must retry against
the latest field state. Completion order alone must not determine the winner;
evidence observation time and field policy do.

### 13.3 Profile Revision

Every change to a current value, resolution state, conflict set, or manual-clear
barrier must increment a monotonically increasing profile revision and update
`updated_at`.

Adding purely duplicate evidence that changes no field resolution may leave the
profile revision unchanged.

## 14. Agent Context

The internal bike-profile read used by diagnostic, planning, and execution
agents must not return only unqualified values after inference is enabled.

It must return a compact resolved context such as:

```json
{
  "profile": {
    "schema_version": "bike_profile.v2",
    "brakes": {
      "front": {"mechanism": "disc", "actuation": "mechanical"},
      "rear": {"mechanism": "disc", "actuation": "hydraulic"}
    },
    "drivetrain": {
      "rear_speed_count": 10
    }
  },
  "field_states": {
    "brakes.rear.actuation": {
      "resolution_state": "resolved",
      "effective_confidence": "high",
      "source_type": "image_inference",
      "observed_at": "2026-07-10T12:00:00Z"
    },
    "drivetrain.rear_speed_count": {
      "resolution_state": "disputed",
      "effective_confidence": "medium",
      "source_type": "derived_resolution",
      "observed_at": "2026-07-10T12:00:00Z"
    }
  },
  "conflicts": [
    {
      "field_path": "drivetrain.rear_speed_count",
      "current_value": 10,
      "candidate_values": [11]
    }
  ]
}
```

The agent context must not include the full claim ledger, inference prompts,
long evidence explanations, model identifiers, or raw confidence scores by
default. Phase-specific code may retrieve supporting evidence when relevant
through a later canonical interface.

The existing internal `get_bike_profile` behavior must be updated or replaced
before agents rely on auto-inferred values.

## 15. Safety, Accuracy, and Privacy

### 15.1 Safety

- Safety-sensitive diagnosis must not depend only on an image-inferred profile
  value.
- A high-confidence brake, wheel-retention, suspension, frame-material, or
  electric-assist fact does not bypass safety policy.
- A disputed safety-relevant field must be treated as insufficiently resolved
  when the field matters to guidance.
- Profile inference must not turn condition observations into durable
  configuration facts.

### 15.2 Accuracy

- The extractor must be allowed to abstain freely.
- Automatic mutation must be enabled field by field after evaluation.
- Exact measurements must not be inferred from apparent pixel dimensions.
- Exact product identity must not be inferred from superficial visual
  similarity for automatic application.
- Position and installedness errors are treated as profile-corruption errors,
  not minor classification errors.

### 15.3 Privacy and Ownership

- Every inference run must use server-resolved ownership and bike association.
- Model input must contain only artifacts and caption content required for the
  run.
- The system must not infer or store people, faces, locations, license plates,
  home details, frame serial numbers, or unrelated image content as profile
  facts.
- EXIF location and capture-time metadata must not be used for profile
  resolution.
- Logs must not contain raw image bytes, signed storage URLs, full model output,
  or prompt text.

## 16. Failure and Retry Behavior

| Failure | Required behavior |
|---|---|
| Model timeout or provider failure | Mark run retryable; leave profile unchanged; do not fail diagnostic turn. |
| Artifact unavailable | Mark run retryable or failed according to artifact state; leave profile unchanged. |
| Schema-invalid output | Fail run; create no claims; record validation metrics. |
| Valid output with no claims | Complete as abstained; no profile change. |
| One or more policy-ineligible claims | Complete run; retain valid claims with pending/conflict disposition; apply eligible claims. |
| Transaction conflict | Reload latest profile and retry deterministic resolution. |
| Permanent retry exhaustion | Mark run failed; preserve diagnostic behavior and surface only operational telemetry. |

Inference failure is not a user-facing diagnostic error. The app may continue
without any automatic profile change.

## 17. Persistence Requirements

The persistence implementation must represent these concepts:

- current `bike_profile.v2` projection
- per-field resolution metadata
- unified bike fact claims
- profile inference runs, their versions, idempotency, status, and timing
- evidence references from claims to app-owned artifact IDs
- profile revision and manual-clear barriers

Recommended app-owned identifiers use distinct prefixes, for example:

- `bfc_` for bike fact claims
- `pir_` for profile inference runs

Exact table names and DDL belong in a follow-up database spec. The current
profile projection should remain typed and queryable rather than existing only
as an opaque model-output JSON blob.

ADK memory and ADK session state must not be the source of truth for any of
these concepts.

## 18. Testing and Evaluation

### 18.1 Deterministic Backend Tests

Backend unit tests must cover:

- field registry validation and unknown-path rejection
- inference-run idempotency
- strict model-output validation
- empty-field auto-fill
- corroborating claims
- inferred-to-inferred replacement
- configured installed-component replacement of an older manual value
- protection of stable manual identity fields
- manual edit and manual-clear barriers
- stale background completion after a manual edit
- explicit front/rear scope isolation
- mixed brake, wheel, and tire configurations
- loose replacement parts and packaging not mutating the profile
- derived-count agreement and disagreement
- conflict persistence and agent context serialization
- atomic profile revision and claim disposition updates
- retry behavior after transaction conflicts

Unit tests must use deterministic extractor fakes and must not assert on exact
LLM wording.

### 18.2 Image Inference Evaluations

Model behavior belongs in `evals/bike-doc`, not pytest assertions over model
responses.

The evaluation dataset must include labeled examples for:

- clear whole-bike views
- front-only and rear-only component views
- mixed front/rear brake and tire configurations
- partially occluded components
- readable and unreadable manufacturer/model markings
- exact tire and wheel-size markings
- ambiguous sprocket and chainring counts
- multiple bikes in one image
- loose replacement parts, product packaging, screenshots, and manuals
- images containing no bike
- user captions explaining that a part is or is not installed
- manual-value conflicts and newer component replacements
- safety-relevant components without sufficient detail

Required metrics are measured per field and evidence class:

- normalized claim precision and recall
- abstention correctness
- front/rear position accuracy
- installed-versus-loose/reference classification accuracy
- automatic-fill precision and coverage
- automatic-overwrite precision and coverage
- exact manufacturer/model false-positive rate
- conflict-detection accuracy
- manual-correction rate after automatic mutation
- model latency, token use, and run cost

In production, automatic mutation for a field must remain disabled until its
field/evidence class meets the configured precision gate on held-out evaluation
data. A non-production development deployment may instead use the provisional
`bootstrap-v1` policy defined in Section 12.3.1 while evaluations are being
built.

### 18.3 Regression Evaluation

Changes to the extractor prompt, output schema, model, image preprocessing, or
field registry require a version increment and regression comparison against
the prior accepted evaluation baseline.

## 19. Rollout

Rollout must proceed in stages:

1. **Shadow extraction**: run inference and store claims without changing
   profiles.
2. **Development bootstrap (non-production only)**: enable the conservative,
   versioned `bootstrap-v1` field-policy bundles to exercise end-to-end profile
   mutation and manual-correction behavior before comprehensive evaluation.
3. **Calibrate**: label failures, establish per-field score mappings, and set
   precision gates using both shadow and development-bootstrap results.
4. **Auto-fill low-risk fields**: after the relevant held-out precision gates
   pass, enable qualified empty-field application field by field in production.
5. **Auto-fill structured components**: enable qualified positioned brake,
   rolling-system, drivetrain, suspension, cockpit, seating, and electric facts.
6. **Inference replacement**: enable newer inferred installed-configuration
   claims to replace older inferred values.
7. **Selected manual replacement**: enable newer direct installed evidence to
   replace older manual values only for field-policy-approved configuration
   fields.
8. **Ongoing monitoring**: review correction, conflict, abstention, and
   field-specific false-positive rates after every extractor version change.

Stable-identity manual overwrites and user-managed field writes remain disabled
for image inference.

## 20. Production Observability

The backend must emit structured operational events or metrics for:

- inference run started, completed, abstained, failed, and retried
- schema validation failure
- number of model claims returned
- claims applied, supporting, pending, conflicting, superseded, and rejected
- profile mutations by field path and source transition
- manual corrections of previously inferred fields
- inference latency, resolver latency, and provider cost where available

Metrics must use stable field paths and version identifiers. Logs should use
stable event names and must not include raw media or sensitive model input.

## 21. Canonical Example Scenarios

### 21.1 Clear Rear Hydraulic Disc Brake

1. The user submits a clear rear-caliper photo in a repair turn.
2. The extractor emits rear-only `mechanism: disc` and
   `actuation: hydraulic` claims with direct visual evidence.
3. The front brake is not visible, so the extractor abstains on front fields.
4. The resolver auto-fills or supersedes the rear brake if policy passes.
5. No whole-bike brake type is inferred.

### 21.2 Mixed Brake Configuration

1. Separate clear views show a mechanical front disc and hydraulic rear disc.
2. The resolver stores each positioned assembly independently.
3. The legacy aggregate `brake_type` is null because the ends differ.
4. Agents receive both values and must reason about the relevant end.

### 21.3 Old Manual Configuration Replaced

1. The profile contains an older manual rear-brake actuation value of
   `mechanical`.
2. A newer clear photo shows an installed hydraulic rear brake.
3. The field is `installed_configuration`, evidence is newer and direct, and
   the calibrated overwrite policy passes.
4. The resolver applies `hydraulic`, supersedes the old claim, and retains both
   claims as history.

### 21.4 User Corrects a Model Inference

1. Image inference sets rear speed count to 10.
2. The user manually changes it to 11.
3. The manual value applies immediately.
4. Retrying the old image inference cannot change it back.
5. A later clear, newly submitted cassette image may create a new claim and be
   evaluated against the manual value under overwrite policy.

### 21.5 Loose Replacement Part

1. The user submits a photo of a boxed hydraulic caliper beside the bike.
2. The extractor classifies it as `loose_component` or
   `packaging_or_reference`.
3. Manufacturer/model claims may be retained as pending evidence if useful.
4. The installed brake profile is not changed.

### 21.6 Tire Sidewall

1. A close image clearly shows `700 x 38C` and `38-622` on the rear tire.
2. The extractor emits rear tire-size claims with `readable_marking` evidence.
3. The resolver updates only the rear tire.
4. The front tire and rim remain unchanged unless independently supported.

## 22. Acceptance Criteria

The feature is implemented only when all of the following are true:

- Every image submitted in an accepted known-bike turn is processed
  idempotently or produces an explicit failed/abstained run state.
- The extractor uses isolated structured context and supports abstention.
- The V2 schema represents independent front/rear brakes, rolling systems, and
  structured drivetrain components.
- All automatic mutations are backed by persisted claims and field-resolution
  metadata.
- Only the deterministic backend resolver mutates profile values.
- Qualified claims can auto-fill and, where field policy permits, auto-overwrite
  current values without routine user confirmation.
- Manual corrections and clears cannot be undone by older or repeated evidence.
- Exact scope, installedness, and front/rear isolation are enforced.
- Agents receive compact confidence and conflict context with resolved values.
- Safety and exact compatibility rules do not treat inference as independent
  proof.
- Production field-specific automatic mutation remains gated by held-out model
  evaluations; non-production development may use the explicit provisional
  bootstrap policy while those evaluations are incomplete.
- Inference failures never fail or block the normal diagnostic turn.
