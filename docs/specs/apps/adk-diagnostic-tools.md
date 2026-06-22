# Bike Doc ADK Diagnostic Tool Contracts Spec

Status: Draft v0.1
Last updated: 2026-06-21

This spec defines the internal backend tools available to the diagnostic ADK
agent. These are not public HTTP endpoints and are not exposed to the Android
client. The public contract remains `docs/specs/openapi.yaml`.

The V1 diagnostic tool surface is intentionally narrow: it gives the agent
access to app-owned context, diagnostic artifacts, structured follow-up
requests, safety escalation, and report persistence. Planning, pricing, repair
instructions, and reference retrieval are deferred to later phase-specific
contracts.

## References

- Product design: `docs/specs/bike-doc.md`
- Backend scaffold: `docs/specs/apps/api.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Diagnostic DB schema: `docs/specs/apps/api-db-diagnostic.md`
- Diagnostic event and SSE semantics:
  `docs/specs/apps/api-events-diagnostic.md`
- Diagnostic report schema: `docs/specs/apps/diagnostic-report-v1.md`
- Public API contract: `docs/specs/openapi.yaml`

## Scope

In scope for V1 diagnostic tools:

- `get_bike_profile`
- `lookup_repair_history`
- `list_diagnostic_artifacts`
- `request_diagnostic_input`
- `raise_safety_flag`
- `save_diagnostic_report`

Deferred from V1 diagnostic tools:

- `lookup_tool_catalog`
- `price_lookup`
- `lookup_repair_reference`
- `lookup_diagnostic_reference`
- user-owned tool inventory lookup
- parts availability or compatibility lookup
- repair instruction lookup
- shop referral search or booking

`lookup_diagnostic_reference` may become useful later for curated diagnostic
criteria, symptom tables, or manual-backed red flags. It is intentionally not
part of V1 so the first diagnostic slice can validate app data, user evidence,
safety state, and report persistence without adding retrieval quality as a
dependency.

## Boundary Rules

ADK tools are internal wrappers over backend services or provider interfaces.
They must not:

- issue SQL directly
- import route modules
- expose ADK session IDs to public API payloads
- return signed media URLs unless an explicit provider contract requires it
- call public OpenAPI route handlers as a shortcut
- perform planning, pricing, or repair-instruction behavior

Tool implementations live under:

```text
apps/api/src/bike_doc_api/adk/tools/
```

Expected modules:

```text
adk/tools/bike_profile.py
adk/tools/repair_history.py
adk/tools/artifacts.py
adk/tools/input_requests.py
adk/tools/safety.py
adk/tools/reports.py
```

Import direction must follow `api.md`:

```text
adk tools -> services/providers
```

Tool input schemas are internal Pydantic models, not OpenAPI request models.
Tool output schemas may reuse internal Pydantic models that map cleanly to
public schema concepts, but public route schemas must remain owned by
`schemas/`.

## Authorization And Session Context

The orchestration layer invokes diagnostic tools with a server-owned execution
context containing:

- authenticated `user_id`
- app-owned `repair_session_id`
- active phase, expected to be `diagnostic`
- app-owned diagnostic phase session/archive ID

The agent must not provide or choose `user_id`. Tool inputs may include
`repair_session_id` for explicitness, but implementations must verify it
matches the orchestration context.

Owner-scoped resources that are missing or not owned by the authenticated user
must return a `not_found` tool error rather than leaking existence through an
authorization error.

## Common Tool Result Shape

Each tool returns either a success payload or a structured tool error.
Exceptions may be used internally, but the ADK-facing wrapper should normalize
known domain failures.

Success shape:

```json
{
  "ok": true,
  "data": {}
}
```

Error shape:

```json
{
  "ok": false,
  "error": {
    "code": "not_found",
    "message": "Repair session was not found."
  }
}
```

Allowed common error codes:

| Code | Meaning |
|---|---|
| `not_found` | Resource is missing or not owned by the authenticated user. |
| `invalid_phase` | The repair session is not in `diagnostic`. |
| `stale_session` | The active diagnostic phase session no longer matches the orchestration context. |
| `validation_error` | Tool input or generated payload is invalid. |
| `artifact_not_found` | An artifact ID is missing, not owned, or not attached to the session. |
| `report_validation_failed` | The diagnostic report cannot be mapped to `DiagnosticReportV1`. |
| `safety_policy_violation` | A safety flag or report conflicts with server safety rules. |
| `internal_error` | Unexpected backend failure. |

Known validation and state errors should be persisted as product events by the
service/orchestration layer when they affect the user-visible turn.

## Tool: `get_bike_profile`

Purpose: return stable bike context for the active repair session so the agent
does not re-ask static profile questions.

Owner: `adk/tools/bike_profile.py`

Service: `services/repair_sessions.py` plus `services` or repository access
behind a bike-profile read service. The tool must not query the database
directly.

Input schema:

```json
{
  "repair_session_id": "rs_123"
}
```

Rules:

- `repair_session_id` must match the orchestration context.
- Session must be owned by the authenticated user.
- Session phase must be `diagnostic`.
- Returned profile must be the bike attached to the repair session.

Output schema:

```json
{
  "ok": true,
  "data": {
    "bike_profile": {
      "id": "bike_123",
      "display_name": "Alex's gravel bike",
      "make": "Surly",
      "model": "Straggler",
      "model_year": 2021,
      "bike_type": "gravel",
      "frame_material": "steel",
      "drivetrain": "Shimano 2x10",
      "brake_type": "mechanical_disc",
      "wheel_size": "700c",
      "tire_size": "700x38",
      "notes": "Commuter setup with rear rack."
    },
    "user_skill_level": "beginner"
  }
}
```

Error behavior:

| Case | Error code |
|---|---|
| Session missing or not owned. | `not_found` |
| Session is not in diagnostic phase. | `invalid_phase` |
| Orchestration phase session mismatch. | `stale_session` |

## Tool: `lookup_repair_history`

Purpose: return relevant prior repairs and service records for the session's
bike.

Owner: `adk/tools/repair_history.py`

Service: `services/repair_sessions.py` and `services` or repository access
behind repair-history read behavior.

Input schema:

```json
{
  "repair_session_id": "rs_123",
  "component_terms": ["rear derailleur", "chain", "cassette"],
  "limit": 5
}
```

Rules:

- `repair_session_id` must match the orchestration context.
- `component_terms` is optional. When omitted or empty, return the most recent
  history for the bike.
- `limit` defaults to `5` and must be between `1` and `20`.
- Results must only come from the bike attached to the active repair session.

Output schema:

```json
{
  "ok": true,
  "data": {
    "entries": [
      {
        "id": "hist_123",
        "bike_id": "bike_123",
        "repair_session_id": "rs_100",
        "title": "Rear shifting adjustment",
        "summary": "Indexed rear derailleur and replaced frayed shift cable.",
        "components": ["rear derailleur", "shift cable"],
        "parts_used": ["shift cable"],
        "tools_used": ["hex key", "cable cutter"],
        "mileage": null,
        "service_date": "2026-05-12",
        "created_at": "2026-05-12T18:30:00Z"
      }
    ]
  }
}
```

Error behavior:

| Case | Error code |
|---|---|
| Session missing or not owned. | `not_found` |
| Session is not in diagnostic phase. | `invalid_phase` |
| Invalid `limit` or malformed terms. | `validation_error` |

## Tool: `list_diagnostic_artifacts`

Purpose: return diagnostic-session artifact metadata so the agent can cite
photos and other evidence in questions, safety flags, and the final report.

Owner: `adk/tools/artifacts.py`

Service: `services/artifacts.py`

Input schema:

```json
{
  "repair_session_id": "rs_123",
  "purpose": "diagnostic_photo"
}
```

Rules:

- `repair_session_id` must match the orchestration context.
- `purpose` is optional and defaults to `diagnostic_photo`.
- V1 only returns artifact metadata, not storage provider objects.
- Artifacts must be owned by the authenticated user and attached to the active
  repair session.

Output schema:

```json
{
  "ok": true,
  "data": {
    "artifacts": [
      {
        "id": "art_123",
        "purpose": "diagnostic_photo",
        "media_type": "image",
        "mime_type": "image/jpeg",
        "filename": "rear-derailleur.jpg",
        "byte_size": 842113,
        "status": "ready",
        "width": 1600,
        "height": 1200,
        "duration_seconds": null,
        "rejection_reason": null,
        "created_at": "2026-06-21T17:03:00Z"
      }
    ]
  }
}
```

Error behavior:

| Case | Error code |
|---|---|
| Session missing or not owned. | `not_found` |
| Session is not in diagnostic phase. | `invalid_phase` |
| Unsupported purpose for diagnostic V1. | `validation_error` |

## Tool: `request_diagnostic_input`

Purpose: let the agent ask the user for specific missing evidence through a
structured app-owned input request instead of relying only on prose.

Owner: `adk/tools/input_requests.py`

Service: `services/turns.py` or `services/repair_sessions.py`, plus
`services/events.py` for persisted `input.requested` events.

Input schema:

```json
{
  "repair_session_id": "rs_123",
  "type": "photo",
  "prompt": "Upload a clear rear-drive-side photo showing the derailleur, chain, cassette, and hanger.",
  "required": true,
  "accepted_media_types": ["image/jpeg", "image/png"],
  "choices": [],
  "min_artifacts": 1,
  "max_artifacts": 3
}
```

Rules:

- `repair_session_id` must match the orchestration context.
- `type` must be one of `text`, `photo`, `multiple_choice`, `confirmation`, or
  `none`. Diagnostic V1 must not request `decision`; decisions belong outside
  the diagnostic phase.
- `prompt` must be user-readable and non-empty unless `type` is `none`.
- `photo` requests must include accepted image media types.
- `multiple_choice` requests must include at least two choices.
- The service must persist the request as `repair_sessions.current_input_request`.
- The service must persist an `input.requested` event before or while streaming
  the response to the client.

Output schema:

```json
{
  "ok": true,
  "data": {
    "input_request": {
      "id": "req_123",
      "type": "photo",
      "prompt": "Upload a clear rear-drive-side photo showing the derailleur, chain, cassette, and hanger.",
      "required": true,
      "accepted_media_types": ["image/jpeg", "image/png"],
      "choices": [],
      "min_artifacts": 1,
      "max_artifacts": 3,
      "created_at": "2026-06-21T17:04:00Z"
    },
    "event_id": "evt_123",
    "event_sequence": 12
  }
}
```

Error behavior:

| Case | Error code |
|---|---|
| Session missing or not owned. | `not_found` |
| Session is not in diagnostic phase. | `invalid_phase` |
| Invalid request type, prompt, media types, choices, or artifact bounds. | `validation_error` |
| Orchestration phase session mismatch. | `stale_session` |

## Tool: `raise_safety_flag`

Purpose: persist a safety concern as soon as it is identified, even before the
final diagnostic report is saved.

Owner: `adk/tools/safety.py`

Service: `services/safety.py`, `services/repair_sessions.py`, and
`services/events.py`.

Input schema:

```json
{
  "repair_session_id": "rs_123",
  "safety_flag": {
    "code": "brake_failure_suspected",
    "severity": "blocking",
    "phase": "diagnostic",
    "message": "The brake issue may prevent safe stopping and should be inspected in person before riding.",
    "blocks_repair_instructions": true
  }
}
```

Rules:

- `repair_session_id` must match the orchestration context.
- `phase` must be `diagnostic`.
- `severity` must be one of `info`, `caution`, `warning`, or `blocking`.
- `blocking` flags must set `blocks_repair_instructions: true`.
- The safety service owns normalization, deduplication, and session
  `safety_state` updates.
- The service must persist a `safety.escalated` event when the active safety
  state changes or a materially new flag is added.

Output schema:

```json
{
  "ok": true,
  "data": {
    "safety_state": "blocked",
    "active_safety_flags": [
      {
        "code": "brake_failure_suspected",
        "severity": "blocking",
        "phase": "diagnostic",
        "message": "The brake issue may prevent safe stopping and should be inspected in person before riding.",
        "blocks_repair_instructions": true
      }
    ],
    "event_id": "evt_124",
    "event_sequence": 13
  }
}
```

Error behavior:

| Case | Error code |
|---|---|
| Session missing or not owned. | `not_found` |
| Session is not in diagnostic phase. | `invalid_phase` |
| Invalid flag shape or unsupported severity. | `validation_error` |
| Blocking flag does not block repair instructions. | `safety_policy_violation` |

## Tool: `save_diagnostic_report`

Purpose: persist the completed diagnostic report, update session report and
safety state, and allow orchestration to transition out of the diagnostic
phase.

Owner: `adk/tools/reports.py`

Service: `services/reports.py`, `services/safety.py`,
`services/repair_sessions.py`, and `services/events.py`.

Input schema:

```json
{
  "repair_session_id": "rs_123",
  "report": {
    "schema_version": "diagnostic_report.v1",
    "primary_diagnosis": {
      "component": "rear derailleur hanger",
      "issue": "Possibly bent, causing inconsistent shifting under load",
      "confidence": "medium",
      "diy_suitability": "caution"
    },
    "alternate_hypotheses": [
      {
        "component": "shift cable",
        "issue": "Cable friction or stretch",
        "confidence": "low",
        "ruled_out_by": "Recent cable replacement in repair history"
      }
    ],
    "evidence_summary": "User reports chain skipping under load in middle gears. Diagnostic photo shows the rear derailleur appears slightly out of plane.",
    "key_artifact_ids": ["art_123"],
    "user_skill_level": "beginner",
    "safety_flags": []
  },
  "summary": "Likely rear shifting alignment issue; hanger damage remains possible."
}
```

Rules:

- `repair_session_id` must match the orchestration context.
- Session must be in `diagnostic`.
- The service, not the agent, sets public `diagnostic_session_id` from the
  app-owned diagnostic phase session/archive ID.
- The mapped report must validate against
  `docs/specs/apps/diagnostic-report-v1.md`.
- `key_artifact_ids` must belong to the authenticated user and be attached to
  the repair session.
- Report safety flags and session active safety flags must be reconciled by
  `services/safety.py`.
- The service must persist a phase report envelope with:
  - `type: diagnostic`
  - `phase: diagnostic`
  - `schema_version: diagnostic_report.v1`
- The service must update the session's latest diagnostic report reference.
- The service must persist `phase.report.created` and, when appropriate,
  `phase.transitioned` events.

Output schema:

```json
{
  "ok": true,
  "data": {
    "report_id": "rpt_123",
    "schema_version": "diagnostic_report.v1",
    "diagnostic_session_id": "phs_123",
    "safety_state": "ok",
    "safety_flags": [],
    "phase_report_created_event_id": "evt_130",
    "phase_report_created_event_sequence": 19,
    "phase_transitioned_event_id": "evt_131",
    "phase_transitioned_event_sequence": 20
  }
}
```

Error behavior:

| Case | Error code |
|---|---|
| Session missing or not owned. | `not_found` |
| Session is not in diagnostic phase. | `invalid_phase` |
| Orchestration phase session mismatch. | `stale_session` |
| Report payload does not map to `DiagnosticReportV1`. | `report_validation_failed` |
| Artifact ID is missing, not owned, or not attached to the session. | `artifact_not_found` |
| Report safety flags violate server safety rules. | `safety_policy_violation` |

## Deferred Tool Details

The following tools are intentionally excluded from the V1 diagnostic tool
contract.

| Tool | Deferred because |
|---|---|
| `lookup_tool_catalog` | Tool requirements belong to planning and cost estimation, not diagnosis. |
| `price_lookup` | Pricing depends on the diagnosed repair plan and belongs to planning. |
| `lookup_repair_reference` | Repair instructions and torque/spec references belong to execution or later reference-backed planning. |
| `lookup_diagnostic_reference` | Useful later, but V1 should not depend on curated/RAG diagnostic retrieval. |
| user-owned tool inventory lookup | V1 does not maintain per-user owned-tool inventory. |
| parts availability or compatibility lookup | Requires planning scope and stronger product data guarantees. |
| shop referral search or booking | Shop referral report behavior is outside the diagnostic vertical slice. |

The diagnostic agent may still ask the user for missing evidence and may choose
low confidence or shop recommendation when authoritative reference data is not
available. It must not invent manufacturer-specific specifications, torque
values, or repair instructions to compensate for deferred tools.

## Testing Requirements

Each diagnostic tool must have unit tests with fake services. Tests should not
make model calls.

Minimum coverage:

- owner-scoped session access
- invalid phase rejection
- stale phase session rejection where applicable
- input validation for each schema
- service call boundary: tools call services/providers, not repositories
- `request_diagnostic_input` persists current input request and event through
  fake services
- `raise_safety_flag` enforces blocking safety behavior
- `save_diagnostic_report` validates payload, artifact ownership, safety
  rules, and emitted event metadata

Contract tests should verify internal tool schemas remain compatible with the
diagnostic report and event specs without generating OpenAPI endpoint stubs for
ADK tools.

## Definition Of Done

- Every V1 diagnostic tool has an explicit input schema, output schema, service
  owner, and error behavior.
- Deferred tools are listed and excluded from diagnostic V1 implementation.
- Implementers can build thin ADK tool wrappers without inventing behavior.
- Tool tests can run without ADK model calls.
