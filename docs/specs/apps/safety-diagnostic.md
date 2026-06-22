# Bike Doc Diagnostic Safety Spec

Status: Draft v0.1
Last updated: 2026-06-22

This spec defines the V1 safety behavior for the diagnostic vertical slice.
Safety is a server invariant owned by `services/safety.py`, not an ADK prompt
convention. The V1 design is intentionally small: fixed diagnostic flag codes,
simple severity rules, highest-severity session state derivation, and explicit
validation before persistence.

`docs/specs/openapi.yaml` remains the canonical public API contract. Diagnostic
safety flags use the OpenAPI `SafetyFlag` shape exactly:

- `code`
- `severity`
- `phase`
- `message`
- `blocks_repair_instructions`

## References

- Product safety policy: `docs/specs/bike-doc.md`
- Backend scaffold and service boundaries: `docs/specs/apps/api.md`
- Diagnostic API behavior: `docs/specs/apps/api-diagnostic.md`
- Diagnostic DB schema: `docs/specs/apps/api-db-diagnostic.md`
- Diagnostic event semantics: `docs/specs/apps/api-events-diagnostic.md`
- Diagnostic report schema: `docs/specs/apps/diagnostic-report-v1.md`
- ADK diagnostic tool contracts: `docs/specs/apps/adk-diagnostic-tools.md`
- Public API contract: `docs/specs/openapi.yaml`
- Diagnostic implementation plan:
  `docs/specs/apps/diagnostic-implementation-plan.md`

## Scope

In scope for V1:

- Diagnostic safety flag code list.
- Safety flag validation and normalization.
- Deduplication and reconciliation between immediate tool-raised flags and
  persisted diagnostic report flags.
- Mapping active flags to `repair_sessions.safety_state`.
- Persistence and event requirements needed by the later decision endpoint.
- Tool-facing and report-persistence error behavior.

Out of scope for V1:

- Numeric risk scoring.
- Expiration, resolution, lifecycle, or override workflows for safety flags.
- Per-flag acknowledgement persistence.
- Curated repair-reference lookup as a prerequisite for safety validation.
- Component-specific micro-taxonomies beyond the fixed V1 codes below.
- ADK ownership of safety validation or safety-state derivation.

## V1 Diagnostic Flag Codes

Diagnostic V1 accepts only these `SafetyFlag.code` values:

| Code | Use when |
|---|---|
| `frame_or_fork_damage_suspected` | The frame, fork, steerer, dropout, or structural area may be cracked, bent, compromised, or unsafe to ride. |
| `brake_failure_suspected` | A brake may be unable to stop the bike reliably, including severe fluid leaks, cable failure, detached parts, or unexplained near-total loss of braking. |
| `carbon_damage_suspected` | A carbon frame, fork, bar, seatpost, rim, crank, or other carbon component may be cracked, crushed, delaminated, or impact damaged. |
| `ebike_electrical_concern` | An e-bike battery, motor, wiring harness, display, charger, or controller concern may involve electrical, thermal, water-ingress, or crash-damage risk. |
| `suspension_internal_concern` | Suspension internals, air springs, dampers, stanchions, crowns, or pressurized suspension components may require specialist service. |
| `safety_critical_fastener_damaged` | A stripped, rounded, missing, cracked, loose, or otherwise damaged safety-critical fastener affects brakes, steering, cockpit, wheels, suspension, or drivetrain retention. |
| `uncertain_torque_spec` | A safety-critical torque value, tightening sequence, compound, or manufacturer-specific procedure is needed but not known with confidence. |
| `contradictory_evidence` | User statements, photos, history, or observations materially conflict with the working diagnosis in a way that affects safety. |
| `insufficient_evidence_for_safe_guidance` | The agent can identify a possible safety-relevant issue but lacks enough evidence to give safe next-step guidance beyond inspection/referral. |
| `unsafe_riding_condition` | The bike appears unsafe to ride for a broad reason not better represented by another V1 code. |

Unknown codes are invalid in diagnostic V1. Later specs may add codes, but V1
implementations must not accept free-form safety taxonomies.

## Severity Rules

Diagnostic V1 supports only the OpenAPI severity values:

| Severity | Meaning | Decision effect | Session state effect |
|---|---|---|---|
| `info` | Non-blocking note that should travel with the report. | `decision: diy` is allowed without acknowledgement. | Maps to `ok` unless a higher active severity exists. |
| `caution` | Low-to-moderate safety concern where DIY may remain reasonable depending on later planning. | `decision: diy` is allowed without acknowledgement. | Maps to `caution` unless a higher active severity exists. |
| `warning` | Significant safety concern. Shop referral should be recommended or strongly considered. | `decision: diy` requires `acknowledged_safety_flags: true`. | Maps to `shop_recommended` unless a blocking flag is active. |
| `blocking` | Safety-critical concern. The backend must not allow DIY to proceed while the flag is active. | `decision: diy` is rejected with `blocking_safety_flag`, regardless of acknowledgement. | Maps to `blocked`. |

Highest active severity wins for `repair_sessions.safety_state`:

1. Any active `blocking` flag means `blocked`.
2. Else any active `warning` flag means `shop_recommended`.
3. Else any active `caution` flag means `caution`.
4. Else `ok`.

`info` flags do not change the session above `ok`.

## Repair Instruction Blocking

`blocks_repair_instructions` controls whether the backend must avoid
step-by-step DIY repair instructions for the flagged condition.

Rules:

- `blocking` flags must set `blocks_repair_instructions: true`.
- `warning` flags may set `blocks_repair_instructions: true` when step-by-step
  guidance would be unsafe without in-person inspection.
- `warning` flags may set `blocks_repair_instructions: false` when DIY remains
  possible after explicit acknowledgement.
- `caution` and `info` flags should set `blocks_repair_instructions: false`.
  If a diagnostic condition truly blocks repair instructions, it should be
  raised as `warning` or `blocking`.

For diagnostic V1 validation, `blocking` with
`blocks_repair_instructions: false` is always a safety policy violation.
`caution` or `info` with `blocks_repair_instructions: true` is accepted only as
an advisory flag and must not make the session `blocked`; diagnostic generators
should not produce that combination.

## Phase Rules

Diagnostic V1 safety flags normally use `phase: diagnostic`.

The safety service must reject a non-diagnostic `phase` in:

- `raise_safety_flag` tool input.
- `save_diagnostic_report` report payloads.
- diagnostic `PhaseReportEnvelope.safety_flags`.

Future phase-specific specs may explicitly allow other phases. Until then,
diagnostic V1 must not infer cross-phase safety semantics from model output.

## Active Safety Flags

V1 active safety flags are derived from two sources:

- Immediate safety flags raised through the `raise_safety_flag` ADK tool.
- The latest validated and persisted diagnostic report safety flags.

`services/safety.py` owns validation, normalization, deduplication,
reconciliation, `safety_state` derivation, and persistence rules. ADK tools and
agents may propose safety flags, but they are never authoritative.

### Normalization

Before deriving session state, the safety service must normalize each flag:

- Validate the flag against the OpenAPI `SafetyFlag` shape.
- Trim surrounding whitespace from `code`, `severity`, `phase`, and `message`
  before validation.
- Require `message` to remain non-blank after trimming.
- Preserve `blocks_repair_instructions` as a boolean, subject to the repair
  instruction blocking rules above.

Normalization must not invent new codes, change a code to a different code, or
silently downgrade severity.

### Deduplication

Deduplicate active flags by `(code, phase)`.

When duplicate flags have the same `(code, phase)`:

- Keep the highest severity.
- Preserve a useful user-facing `message` from the highest-severity instance.
- Set `blocks_repair_instructions` to `true` if any retained
  highest-severity instance requires it.
- Keep one resulting flag in `repair_sessions.active_safety_flags`.

Severity ordering for deduplication is:

```text
info < caution < warning < blocking
```

Contradictory duplicates inside the same model-produced payload, where the
same `(code, phase)` appears at lower and higher severities, are invalid unless
the service path explicitly performs the normalization above before
persistence. V1 implementation should choose one behavior per entry point and
test it:

- `raise_safety_flag`: normalize against existing active flags.
- `save_diagnostic_report`: fail validation if one report payload contains
  contradictory duplicates.

### Reconciliation

When `raise_safety_flag` succeeds:

1. Validate and normalize the tool-provided flag.
2. Reconcile it with the current `repair_sessions.active_safety_flags` using
   the deduplication rules above.
3. Derive `repair_sessions.safety_state` from the reconciled active flags.
4. Persist the reconciled active flags and safety state.
5. Persist `safety.escalated` if the safety state changes or a materially new
   active flag is added.

When `save_diagnostic_report` succeeds:

1. Validate the report payload safety flags.
2. Validate the report envelope safety flags.
3. Require envelope `safety_flags` and `payload.safety_flags` to be identical.
4. Persist the report's own flags unchanged in `phase_reports.safety_flags`
   and in the report payload after validation.
5. Reconcile the validated report flags with any active flags previously raised
   through `raise_safety_flag`.
6. Persist the reconciled list to `repair_sessions.active_safety_flags`.
7. Derive and persist `repair_sessions.safety_state` from the reconciled active
   flags.

This clarifies the `api-db-diagnostic.md` phase-report rule: in diagnostic V1,
session active flags are updated from the validated report envelope plus any
previously tool-raised active diagnostic flags, reconciled by the safety
service.

V1 does not define flag resolution. A previously raised active flag is not
removed merely because the report omits it. Later lifecycle specs may define
explicit resolution or replacement behavior.

## Validation Rules

The safety service must reject or fail validation for:

- Unknown safety flag `code`.
- Unsupported `severity`.
- Missing required OpenAPI fields.
- Non-diagnostic `phase` in diagnostic V1 inputs.
- Blank `message`.
- Non-boolean `blocks_repair_instructions`.
- `blocking` with `blocks_repair_instructions: false`.
- Contradictory duplicates in one report payload unless that entry point
  explicitly normalizes duplicates before persistence.
- Diagnostic report envelope `safety_flags` differing from
  `payload.safety_flags`.

Expected error behavior:

- Tool-facing malformed flag shape, missing fields, unknown code, unsupported
  severity, blank message, or invalid phase should return `validation_error`.
- Tool-facing safety rule violations, including a blocking flag that does not
  block repair instructions, should return `safety_policy_violation`.
- Invalid generated report payloads must fail before persistence.
- Persisted invalid report payloads discovered during API read are server data
  integrity errors, not client validation errors.

Validation must happen independently of ADK behavior and must be unit-testable
without model calls.

## Persistence Requirements

Persist enough safety data for the later decision endpoint to enforce
`blocking` and `warning` behavior without re-running a model.

Required persisted fields:

- `repair_sessions.active_safety_flags`: reconciled active flags after
  validation, normalization, and deduplication.
- `repair_sessions.safety_state`: derived from the highest active severity.
- `phase_reports.safety_flags`: the validated report envelope flags, unchanged
  by active-flag reconciliation.
- `phase_reports.payload.safety_flags`: the validated
  `DiagnosticReportV1.safety_flags`, identical to envelope `safety_flags`.
- `repair_session_events` rows for `safety.escalated` when required.

`/v1/repair-sessions/{sessionId}/decision` must use the persisted active flags
and safety state. It must not trust a client-provided safety state or ask ADK to
recompute safety.

## Event Requirements

Use the existing public event taxonomy. Do not add a second public safety event
type.

Persist `safety.escalated` when:

- `repair_sessions.safety_state` changes.
- A materially new active safety flag is added, even if the state is unchanged.
- `blocks_repair_instructions` changes from `false` to `true` for an active
  warning or blocking condition.

For diagnostic report completion, event order is:

1. `safety.escalated`, if safety changes.
2. `phase.report.created`.
3. `turn.completed`.
4. `phase.transitioned`, if applicable.

`SafetyEscalatedEventData.safety_flags` must contain the reconciled active
flags after the change. `blocks_repair_instructions` must be `true` if any
active flag blocks repair instructions.

## Examples

### Warning That Requires Acknowledgement

```json
{
  "code": "uncertain_torque_spec",
  "severity": "warning",
  "phase": "diagnostic",
  "message": "A safety-critical torque specification is needed before giving reliable DIY tightening guidance.",
  "blocks_repair_instructions": true
}
```

Expected result:

- Active flag is persisted.
- `repair_sessions.safety_state` becomes `shop_recommended` unless a blocking
  flag is active.
- Later `decision: diy` requires `acknowledged_safety_flags: true`.

### Blocking Brake Concern

```json
{
  "code": "brake_failure_suspected",
  "severity": "blocking",
  "phase": "diagnostic",
  "message": "Do not ride the bike until the braking issue is inspected and repaired by a qualified mechanic.",
  "blocks_repair_instructions": true
}
```

Expected result:

- Active flag is persisted.
- `repair_sessions.safety_state` becomes `blocked`.
- Later `decision: diy` is rejected with `blocking_safety_flag` regardless of
  acknowledgement.

### Invalid Blocking Flag

```json
{
  "code": "brake_failure_suspected",
  "severity": "blocking",
  "phase": "diagnostic",
  "message": "The brake may not stop the bike reliably.",
  "blocks_repair_instructions": false
}
```

Expected result:

- `raise_safety_flag` returns `safety_policy_violation`.
- `save_diagnostic_report` fails before persistence if the flag appears in the
  generated report.

## Definition of Done

- `services/safety.py` can validate diagnostic V1 safety flags without ADK.
- Unit tests cover `info`, `caution`, `warning`, and `blocking` state mapping.
- Unit tests cover malformed flags and safety policy violations.
- Report persistence tests cover envelope and payload safety flag equality.
- Reconciliation tests cover tool-raised flags plus report flags.
- `raise_safety_flag` behavior is tested for validation, persistence,
  deduplication, `safety_state`, and `safety.escalated` emission.
