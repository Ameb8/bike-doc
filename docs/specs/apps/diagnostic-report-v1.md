# Bike Doc Diagnostic Report V1 Schema Spec

Status: Draft v0.1
Last updated: 2026-06-21

This spec defines the diagnostic report payload produced by the diagnostic
agent, persisted in `phase_reports.payload`, and exposed through the public
`PhaseReportEnvelope` API.

`docs/specs/openapi.yaml` remains the canonical public API contract. This
document clarifies implementation rules, validation timing, and the boundary
between public API models and ADK/internal report models.

## References

- Backend scaffold: `docs/specs/apps/api.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Diagnostic DB schema: `docs/specs/apps/api-db-diagnostic.md`
- Public API contract: `docs/specs/openapi.yaml`

## Scope

In scope:

- `DiagnosticReportV1`
- `Diagnosis`
- `AlternateHypothesis`
- `Confidence`
- diagnostic report safety flags
- validation before persistence and before API exposure
- mapping from internal diagnostic output to public API payloads

Out of scope:

- plan, execution, and shop-referral report schemas
- final diagnostic prompts
- ADK session table schemas
- database migrations

## Canonical OpenAPI Shape

`DiagnosticReportV1` must match `components.schemas.DiagnosticReportV1` in
`docs/specs/openapi.yaml`.

Required top-level fields:

| Field | Type | Rules |
|---|---|---|
| `schema_version` | string | Required. Must equal `diagnostic_report.v1`. |
| `primary_diagnosis` | `Diagnosis` | Required. Main diagnostic conclusion. |
| `alternate_hypotheses` | array of `AlternateHypothesis` | Required. Use `[]` when there are no alternates. |
| `evidence_summary` | string | Required. User-readable evidence summary. |
| `key_artifact_ids` | array of string | Required. Use `[]` when no artifacts materially informed the diagnosis. |
| `user_skill_level` | `UserSkillLevel` | Required. One of `unknown`, `beginner`, `intermediate`, `advanced`. |
| `safety_flags` | array of `SafetyFlag` | Required. Use `[]` when there are no safety concerns. |
| `diagnostic_session_id` | string | Required. Opaque internal archive/session reference. See below. |

`Diagnosis` fields:

| Field | Type | Rules |
|---|---|---|
| `component` | string | Required. The bike component or area being diagnosed. |
| `issue` | string | Required. Concise description of the suspected issue. |
| `confidence` | `Confidence` | Required. See allowed values below. |
| `diy_suitability` | string | Optional in OpenAPI. If omitted, clients treat it as `unknown`. Allowed values are `unknown`, `reasonable`, `caution`, `shop_recommended`, `blocked`. |

`AlternateHypothesis` fields:

| Field | Type | Rules |
|---|---|---|
| `component` | string | Required. The component or area for the alternate. |
| `issue` | string | Required. Concise alternate explanation. |
| `confidence` | `Confidence` | Required. See allowed values below. |
| `ruled_out_by` | string or null | Optional and nullable. When present, describes evidence that reduces or eliminates the hypothesis. |

The OpenAPI schema does not define additional diagnostic report fields. Public
serialization must emit only fields declared by `DiagnosticReportV1` and its
nested schemas.

## Confidence Values

Allowed `Confidence` values are:

- `unknown`
- `low`
- `medium`
- `high`

Use `unknown` only when the system has enough information to produce a report
but cannot assign a meaningful likelihood. If the report cannot make a useful
diagnostic statement at all, the agent should request follow-up input instead
of producing a completed diagnostic report.

## Safety Flag Semantics

Diagnostic report safety flags use the OpenAPI `SafetyFlag` schema exactly.
Each flag requires:

| Field | Rules |
|---|---|
| `code` | Stable machine-readable code, for example `brake_failure_suspected`. |
| `severity` | One of `info`, `caution`, `warning`, `blocking`. |
| `phase` | Must be a valid `RepairSessionPhase`; diagnostic reports normally use `diagnostic`. |
| `message` | User-readable safety explanation. |
| `blocks_repair_instructions` | `true` when the backend must not provide DIY repair instructions for the flagged condition. |

Severity meanings:

| Severity | Meaning |
|---|---|
| `info` | Non-blocking note that should travel with the report. |
| `caution` | Low-to-moderate safety concern; DIY may remain reasonable depending on the plan. |
| `warning` | Significant safety concern. A later `decision: diy` requires `acknowledged_safety_flags: true`. |
| `blocking` | Safety-critical concern. The server must reject `decision: diy` with `error.code: blocking_safety_flag` while the flag is active, regardless of acknowledgement. |

`blocks_repair_instructions` must be `true` for every `blocking` flag. It may
also be `true` for a `warning` flag when providing DIY instructions would be
unsafe without in-person inspection.

The report envelope's `safety_flags` and the payload's `safety_flags` must be
identical for diagnostic reports. The service layer derives
`repair_sessions.safety_state` from the active report flags when persisting the
report, following `api-db-diagnostic.md`.

## `diagnostic_session_id`

`diagnostic_session_id` is an internal archive/session reference. It is not a
public ADK session contract and must not require mobile clients to understand
ADK concepts.

Implementation rule:

- Store the app-owned `repair_phase_sessions.id` for the diagnostic phase, as
  specified in `api-db-diagnostic.md`.
- Do not expose a raw ADK table primary key or ADK runner session ID.
- Treat the value as opaque in the public API. Clients may store or display
  reports without parsing this field.

This field lets the backend trace a public diagnostic report back to the
internal phase archive that produced it while preserving the `api.md` boundary:
public routes expose app-owned product resources, not ADK internals.

## Public And Internal Models

Use separate API and ADK/internal models with an explicit mapper.

Expected modules:

```text
apps/api/src/bike_doc_api/schemas/report.py
apps/api/src/bike_doc_api/adk/report_schemas/diagnostic.py
```

`schemas/report.py` owns public API schemas that mirror `openapi.yaml`.
These models are used for route responses, persistence validation, and
contract tests.

`adk/report_schemas/diagnostic.py` owns the structured output requested from
the diagnostic agent. It may include internal fields useful for orchestration,
prompting, evidence ranking, or tool traces, but those fields must not be
serialized directly to the public API.

The mapper from internal output to public `DiagnosticReportV1` must:

- set `schema_version` to `diagnostic_report.v1`
- select exactly one `primary_diagnosis`
- normalize alternate hypotheses into the OpenAPI shape
- normalize confidence values to `unknown`, `low`, `medium`, or `high`
- copy only artifact IDs owned by the target repair session into
  `key_artifact_ids`
- normalize safety flags to the OpenAPI `SafetyFlag` schema
- set `diagnostic_session_id` from `repair_phase_sessions.id`
- drop internal-only fields before public validation

This keeps the public API stable while allowing internal ADK report evolution.

## Validation Rules

Generated diagnostic reports must be validated twice:

1. Before persistence.
2. Before exposure through `/v1/repair-sessions/{sessionId}/reports` or
   `/v1/repair-sessions/{sessionId}/reports/{reportId}`.

Before persistence:

- Validate the mapped payload against the public `DiagnosticReportV1` model.
- Validate the enclosing `PhaseReportEnvelope` fields:
  - `type: diagnostic`
  - `schema_version: diagnostic_report.v1`
  - `phase: diagnostic`
  - `payload.schema_version: diagnostic_report.v1`
- Validate `summary` is non-blank.
- Validate `source_artifact_ids` and `payload.key_artifact_ids` contain only
  artifact IDs owned by the authenticated user and associated with the repair
  session.
- Validate `payload.safety_flags` and envelope `safety_flags` are identical.
- Validate every `SafetyFlag.phase` is compatible with the active report phase.
- Validate every `blocking` safety flag has `blocks_repair_instructions: true`.
- Validate `diagnostic_session_id` resolves to the app-owned diagnostic
  `repair_phase_sessions.id` for the same repair session.

Before API exposure:

- Re-validate the stored payload against the public `DiagnosticReportV1` model.
- Re-validate the stored envelope against the public envelope schema.
- Serialize only public schema fields.
- Do not expose ADK prompts, raw runner events, tool traces, model names,
  private evidence notes, or raw ADK session IDs.

Validation failure before persistence should fail the producing turn and emit a
persisted error event. Validation failure before API exposure should be treated
as a server-side data integrity error, not as a client validation error.

## Example Reports

### Straightforward Low-Risk Diagnosis

```json
{
  "schema_version": "diagnostic_report.v1",
  "primary_diagnosis": {
    "component": "rear derailleur barrel adjuster",
    "issue": "Cable tension appears slightly low, causing hesitation when shifting to larger cogs.",
    "confidence": "high",
    "diy_suitability": "reasonable"
  },
  "alternate_hypotheses": [
    {
      "component": "chain",
      "issue": "A dry or dirty chain can make shifting feel rough.",
      "confidence": "low",
      "ruled_out_by": "The symptom is gear-specific rather than constant across the drivetrain."
    }
  ],
  "evidence_summary": "The user reports slow upshifts after recent cable replacement, and the drivetrain photo shows no obvious damage.",
  "key_artifact_ids": ["art_123"],
  "user_skill_level": "intermediate",
  "safety_flags": [],
  "diagnostic_session_id": "phs_123"
}
```

### Ambiguous Diagnosis Requiring Follow-Up

```json
{
  "schema_version": "diagnostic_report.v1",
  "primary_diagnosis": {
    "component": "rear drivetrain",
    "issue": "Skipping under load may come from drivetrain wear, cable tension, or derailleur hanger alignment.",
    "confidence": "low",
    "diy_suitability": "caution"
  },
  "alternate_hypotheses": [
    {
      "component": "cassette and chain",
      "issue": "Worn chain and cassette teeth can skip when torque increases.",
      "confidence": "medium",
      "ruled_out_by": null
    },
    {
      "component": "rear derailleur hanger",
      "issue": "A mildly bent hanger can cause inconsistent indexing in the middle gears.",
      "confidence": "medium",
      "ruled_out_by": null
    }
  ],
  "evidence_summary": "The user reports skipping only while pedaling hard. The available photo does not clearly show chain wear, cassette tooth profile, or hanger alignment.",
  "key_artifact_ids": ["art_456"],
  "user_skill_level": "beginner",
  "safety_flags": [
    {
      "code": "diagnosis_uncertain_under_load",
      "severity": "caution",
      "phase": "diagnostic",
      "message": "The cause is not clear enough to recommend a specific repair without more evidence.",
      "blocks_repair_instructions": false
    }
  ],
  "diagnostic_session_id": "phs_456"
}
```

In this case the report is schema-valid, but the product may still ask for
follow-up input before moving to planning. If the agent lacks enough evidence
to produce even a low-confidence primary diagnosis, it should request input
instead of creating a report.

### Blocking Safety Diagnosis

```json
{
  "schema_version": "diagnostic_report.v1",
  "primary_diagnosis": {
    "component": "front brake",
    "issue": "The brake may be unable to stop the bike reliably because the hose or caliper appears damaged.",
    "confidence": "medium",
    "diy_suitability": "blocked"
  },
  "alternate_hypotheses": [
    {
      "component": "brake pads",
      "issue": "Severely contaminated pads could also cause very weak braking.",
      "confidence": "low",
      "ruled_out_by": null
    }
  ],
  "evidence_summary": "The user reports near-total loss of braking, and the photo appears to show fluid residue near the front caliper.",
  "key_artifact_ids": ["art_789"],
  "user_skill_level": "beginner",
  "safety_flags": [
    {
      "code": "front_brake_failure_suspected",
      "severity": "blocking",
      "phase": "diagnostic",
      "message": "Do not ride the bike until the front brake is inspected and repaired by a qualified mechanic.",
      "blocks_repair_instructions": true
    }
  ],
  "diagnostic_session_id": "phs_789"
}
```

A report like this must cause the service layer to persist the active safety
flag, update session safety state to `blocked`, emit `safety.escalated`, and
prevent `decision: diy` while the blocking flag remains active.
