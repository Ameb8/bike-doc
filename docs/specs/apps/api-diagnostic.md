# Bike Doc Diagnostic API Delta Spec

Status: Draft v0.1
Last updated: 2026-06-21

This spec defines the public API subset needed for the diagnostic vertical
slice. It is a delta on top of `docs/specs/apps/api.md` and
`docs/specs/openapi.yaml`; it does not introduce ADK-facing public routes or
agent-internal schemas.

## References

- Backend scaffold: `docs/specs/apps/api.md`
- Public API contract: `docs/specs/openapi.yaml`
- Diagnostic report schema: `docs/specs/apps/diagnostic-report-v1.md`
- Diagnostic event and SSE semantics:
  `docs/specs/apps/api-events-diagnostic.md`
- Implementation plan: `docs/specs/apps/diagnostic-implementation-plan.md`
- Product design: `docs/specs/bike-doc.md`

## OpenAPI Delta

`docs/specs/openapi.yaml` remains the canonical public API contract. For this
spec, it was patched narrowly to make diagnostic-slice error responses explicit:

- Add `401 Unauthorized` to secured diagnostic-slice endpoints that previously
  relied only on top-level `security`.
- Add `409 Conflict` to `POST /v1/repair-sessions` for idempotency-key payload
  conflicts.
- Add `404 Not Found` and `409 Conflict` to `POST /v1/artifacts` for missing
  owner-scoped parent resources and artifact idempotency conflicts.
- Add `422 Validation Error` to `GET /v1/repair-sessions/{sessionId}/events`
  for invalid stream parameters.

No schema changes are required for the diagnostic slice. Existing schemas are
sufficient:

- `RepairSessionCreate`
- `RepairSession`
- `TurnCreate`
- `TurnAccepted`
- `ArtifactRef`
- `RepairSessionEvent`
- `PhaseReportEnvelope`
- `DiagnosticReportV1`
- `ErrorResponse`

## Diagnostic Slice Paths

The diagnostic slice includes these existing OpenAPI paths:

| Path | Method | OpenAPI operationId | Diagnostic role |
|---|---:|---|---|
| `/v1/repair-sessions` | `POST` | `createRepairSession` | Start a diagnostic phase for a bike. |
| `/v1/repair-sessions/{sessionId}` | `GET` | `getRepairSession` | Read current session state. |
| `/v1/repair-sessions/{sessionId}/turns` | `POST` | `createRepairSessionTurn` | Submit user text/photo input. |
| `/v1/repair-sessions/{sessionId}/events` | `GET` | `streamRepairSessionEvents` | Replay and stream persisted events. |
| `/v1/artifacts` | `POST` | `uploadArtifact` | Upload diagnostic photos. |
| `/v1/repair-sessions/{sessionId}/reports` | `GET` | `listRepairSessionReports` | List phase reports for a session. |
| `/v1/repair-sessions/{sessionId}/reports/{reportId}` | `GET` | `getRepairSessionReport` | Read one diagnostic report envelope. |

The public API remains app-owned. Route handlers follow the `api.md` boundary:
`api -> services -> repositories/models`, with ADK orchestration hidden behind
services. Public payloads must not expose ADK session objects, prompts, runner
events, or tool internals.

## Stub Policy

No endpoint should be implemented as behavior in Stage 2. Stage 2 is a codegen
spike only: generate isolated code, inspect it, and decide whether generated
models are suitable.

When route implementation begins, these endpoints can be implemented without an
agent call:

- `POST /v1/repair-sessions`
- `GET /v1/repair-sessions/{sessionId}`
- `POST /v1/artifacts`
- `GET /v1/repair-sessions/{sessionId}/events` for persisted replay and
  heartbeat behavior
- `GET /v1/repair-sessions/{sessionId}/reports`
- `GET /v1/repair-sessions/{sessionId}/reports/{reportId}`

`POST /v1/repair-sessions/{sessionId}/turns` may use a deterministic no-agent
stub during early API testing only if it still persists a turn, emits persisted
events, and returns a schema-valid `TurnAccepted`. The stub must not pretend to
be final diagnostic reasoning.

## Common Error Rules

All endpoints require bearer authentication. Missing, expired, malformed, or
unverifiable credentials return:

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Authentication is required."
  }
}
```

Owner-scoped resources that do not exist or are not owned by the authenticated
user return `404 Not Found`, not `403 Forbidden`, to avoid leaking resource
existence.

Validation failures return `422 Validation Error` with `error.code:
validation_error`. State-machine or idempotency-key payload conflicts return
`409 Conflict`.

## `POST /v1/repair-sessions`

Creates a repair session in `phase: diagnostic` for an owned bike profile.

Request:

```json
{
  "bike_id": "bike_123",
  "client_session_id": "mobile-new-repair-001"
}
```

Response `201 Created`:

```json
{
  "id": "rs_123",
  "user_id": "usr_123",
  "bike_id": "bike_123",
  "phase": "diagnostic",
  "status": "created",
  "safety_state": "ok",
  "current_input_request": null,
  "execution_progress": null,
  "latest_reports": {
    "diagnostic_report_id": null,
    "plan_report_id": null,
    "execution_report_id": null,
    "shop_referral_report_id": null
  },
  "latest_event_id": "0",
  "created_at": "2026-06-21T17:00:00Z",
  "updated_at": "2026-06-21T17:00:00Z"
}
```

Expected errors:

| Status | Case | Error code |
|---:|---|---|
| `401` | Missing or invalid bearer token. | `unauthorized` |
| `404` | `bike_id` does not exist for the authenticated user. | `not_found` |
| `409` | Same `client_session_id` is reused with a different request payload. | `idempotency_conflict` |
| `422` | Required `bike_id` is missing or malformed. | `validation_error` |

Retrying the exact same `client_session_id` request returns the original
created session with `201 Created`.

## `GET /v1/repair-sessions/{sessionId}`

Returns the current app-owned session state.

Response `200 OK`:

```json
{
  "id": "rs_123",
  "user_id": "usr_123",
  "bike_id": "bike_123",
  "phase": "diagnostic",
  "status": "awaiting_user",
  "safety_state": "ok",
  "current_input_request": {
    "id": "req_123",
    "type": "photo",
    "prompt": "Upload a rear-drive-side photo of the derailleur.",
    "required": true,
    "accepted_media_types": ["image/jpeg", "image/png"],
    "choices": [],
    "min_artifacts": 1,
    "max_artifacts": 3,
    "created_at": "2026-06-21T17:01:00Z"
  },
  "execution_progress": null,
  "latest_reports": {
    "diagnostic_report_id": null,
    "plan_report_id": null,
    "execution_report_id": null,
    "shop_referral_report_id": null
  },
  "latest_event_id": "4",
  "created_at": "2026-06-21T17:00:00Z",
  "updated_at": "2026-06-21T17:01:00Z"
}
```

Expected errors:

| Status | Case | Error code |
|---:|---|---|
| `401` | Missing or invalid bearer token. | `unauthorized` |
| `404` | Session does not exist or belongs to another user. | `not_found` |

## `POST /v1/repair-sessions/{sessionId}/turns`

Accepts one user turn for the session's current phase. The turn may include
text, artifact IDs, or both. `client_turn_id` is required for idempotency.

Request:

```json
{
  "schema_version": "ai_turn.v1",
  "client_turn_id": "mobile-turn-001",
  "message": {
    "text": "The chain skips when I pedal hard in the middle gears.",
    "artifact_ids": ["art_123"]
  },
  "responds_to_input_request_id": "req_123"
}
```

Response `202 Accepted`:

```json
{
  "turn_id": "turn_123",
  "repair_session_id": "rs_123",
  "start_event_id": "4",
  "event_stream_url": "/v1/repair-sessions/rs_123/events?after=4",
  "session": {
    "id": "rs_123",
    "user_id": "usr_123",
    "bike_id": "bike_123",
    "phase": "diagnostic",
    "status": "running",
    "safety_state": "ok",
    "current_input_request": null,
    "execution_progress": null,
    "latest_reports": {
      "diagnostic_report_id": null,
      "plan_report_id": null,
      "execution_report_id": null,
      "shop_referral_report_id": null
    },
    "latest_event_id": "5",
    "created_at": "2026-06-21T17:00:00Z",
    "updated_at": "2026-06-21T17:02:00Z"
  }
}
```

Expected errors:

| Status | Case | Error code |
|---:|---|---|
| `401` | Missing or invalid bearer token. | `unauthorized` |
| `404` | Session or referenced artifact/input request does not exist for this user. | `not_found` |
| `409` | Session is not accepting diagnostic turns, or `client_turn_id` is reused with different payload. | `session_state_conflict` or `idempotency_conflict` |
| `422` | `schema_version`, `client_turn_id`, or message body is invalid. | `validation_error` |

Retrying the exact same `client_turn_id` request returns the original accepted
turn response with `202 Accepted`.

## `GET /v1/repair-sessions/{sessionId}/events`

Streams persisted repair-session events using `text/event-stream`.
Detailed event identity, cursor, heartbeat, timeout, persistence, and replay
retention rules are defined in `docs/specs/apps/api-events-diagnostic.md`.

Request:

```text
GET /v1/repair-sessions/rs_123/events?after=0&timeout_seconds=30
Accept: text/event-stream
Authorization: Bearer <token>
```

Response `200 OK`:

```text
id: 1
event: turn.started
data: {"id":"1","session_id":"rs_123","turn_id":"turn_123","type":"turn.started","sequence":1,"created_at":"2026-06-21T17:02:00Z","data":{"turn_id":"turn_123","phase":"diagnostic"}}

id: 2
event: assistant.delta
data: {"id":"2","session_id":"rs_123","turn_id":"turn_123","type":"assistant.delta","sequence":2,"created_at":"2026-06-21T17:02:01Z","data":{"text":"I need one photo of the rear derailleur."}}
```

`after` and `Last-Event-ID` use public event IDs. If both are present, `after`
takes precedence. `after=0` replays all retained events. Omitted `after` starts
after the session's current `latest_event_id` and waits for live events.

Diagnostic slice event types:

- `turn.started`
- `assistant.delta`
- `assistant.message.completed`
- `input.requested`
- `artifact.referenced`
- `phase.report.created`
- `phase.transitioned`
- `safety.escalated`
- `turn.completed`
- `error`
- `heartbeat`

`execution.step.updated` is in OpenAPI for the broader product contract but is
not emitted by the diagnostic slice.

Expected errors:

| Status | Case | Error code |
|---:|---|---|
| `401` | Missing or invalid bearer token. | `unauthorized` |
| `404` | Session does not exist or belongs to another user. | `not_found` |
| `422` | `timeout_seconds` is outside `5..120`, or `after` is not a supported cursor value. | `validation_error` |

## `POST /v1/artifacts`

Uploads a media artifact. For diagnostic photos, `purpose` must be
`diagnostic_photo`, `repair_session_id` is required, and `bike_id` must be
omitted.

Request:

```text
POST /v1/artifacts
Content-Type: multipart/form-data

file=@rear-derailleur.jpg
purpose=diagnostic_photo
repair_session_id=rs_123
client_artifact_id=mobile-artifact-001
```

Response `201 Created`:

```json
{
  "artifact": {
    "id": "art_123",
    "user_id": "usr_123",
    "repair_session_id": "rs_123",
    "bike_id": null,
    "purpose": "diagnostic_photo",
    "media_type": "image",
    "mime_type": "image/jpeg",
    "filename": "rear-derailleur.jpg",
    "byte_size": 345678,
    "width": 1600,
    "height": 1200,
    "duration_seconds": null,
    "status": "ready",
    "rejection_reason": null,
    "created_at": "2026-06-21T17:01:30Z"
  }
}
```

Expected errors:

| Status | Case | Error code |
|---:|---|---|
| `401` | Missing or invalid bearer token. | `unauthorized` |
| `404` | Referenced repair session or bike does not exist for this user. | `not_found` |
| `409` | Same `client_artifact_id` is reused with different metadata or content. | `idempotency_conflict` |
| `413` | Uploaded file exceeds the configured size limit. | `payload_too_large` |
| `422` | Purpose/parent-resource rule is violated, file is missing, or MIME type is unsupported. | `validation_error` |

Retrying the exact same `client_artifact_id` upload returns the original
artifact response with `201 Created`.

## `GET /v1/repair-sessions/{sessionId}/reports`

Lists persisted phase reports for a session. During the diagnostic slice, the
only report type expected in normal success responses is `diagnostic`.

Response `200 OK`:

```json
{
  "items": [
    {
      "id": "rpt_123",
      "repair_session_id": "rs_123",
      "type": "diagnostic",
      "schema_version": "diagnostic_report.v1",
      "phase": "diagnostic",
      "summary": "Likely rear derailleur hanger alignment issue causing chain skip.",
      "safety_flags": [],
      "source_artifact_ids": ["art_123"],
      "created_at": "2026-06-21T17:05:00Z",
      "payload": {
        "schema_version": "diagnostic_report.v1",
        "primary_diagnosis": {
          "component": "rear derailleur hanger",
          "issue": "Likely bent or misaligned, causing inconsistent shifting under load.",
          "confidence": "medium",
          "diy_suitability": "caution"
        },
        "alternate_hypotheses": [
          {
            "component": "cassette or chain",
            "issue": "Worn drivetrain can also skip under load.",
            "confidence": "low",
            "ruled_out_by": null
          }
        ],
        "evidence_summary": "User reports skipping under load and provided a rear drivetrain photo.",
        "key_artifact_ids": ["art_123"],
        "user_skill_level": "beginner",
        "safety_flags": [],
        "diagnostic_session_id": "diag_archive_123"
      }
    }
  ],
  "next_cursor": null
}
```

Expected errors:

| Status | Case | Error code |
|---:|---|---|
| `401` | Missing or invalid bearer token. | `unauthorized` |
| `404` | Session does not exist or belongs to another user. | `not_found` |
| `422` | Pagination parameters are invalid. | `validation_error` |

## `GET /v1/repair-sessions/{sessionId}/reports/{reportId}`

Returns one persisted phase report envelope.

Response `200 OK`:

```json
{
  "id": "rpt_123",
  "repair_session_id": "rs_123",
  "type": "diagnostic",
  "schema_version": "diagnostic_report.v1",
  "phase": "diagnostic",
  "summary": "Likely rear derailleur hanger alignment issue causing chain skip.",
  "safety_flags": [],
  "source_artifact_ids": ["art_123"],
  "created_at": "2026-06-21T17:05:00Z",
  "payload": {
    "schema_version": "diagnostic_report.v1",
    "primary_diagnosis": {
      "component": "rear derailleur hanger",
      "issue": "Likely bent or misaligned, causing inconsistent shifting under load.",
      "confidence": "medium",
      "diy_suitability": "caution"
    },
    "alternate_hypotheses": [],
    "evidence_summary": "Photo and symptom pattern point to rear shifting alignment.",
    "key_artifact_ids": ["art_123"],
    "user_skill_level": "beginner",
    "safety_flags": [],
    "diagnostic_session_id": "diag_archive_123"
  }
}
```

Expected errors:

| Status | Case | Error code |
|---:|---|---|
| `401` | Missing or invalid bearer token. | `unauthorized` |
| `404` | Session/report pair does not exist for this user. | `not_found` |

## Diagnostic Completion Behavior

The diagnostic phase is complete when the backend persists a
`PhaseReportEnvelope` with:

- `type: diagnostic`
- `schema_version: diagnostic_report.v1`
- `phase: diagnostic`
- `payload.schema_version: diagnostic_report.v1`

Completion must also:

- Update `repair_sessions.latest_reports.diagnostic_report_id`.
- Persist a `phase.report.created` event.
- Persist a `turn.completed` event for the turn that produced the report.
- Persist `safety.escalated` before or with the report if the diagnostic output
  changes `safety_state`.
- Transition to `phase: planning` only if the product flow chooses immediate
  planning. A diagnostic-only vertical-slice stub may leave the session in
  `phase: diagnostic` with `status: awaiting_user` or `status: running` until
  the event/report specs define the exact transition timing.

## Invariants

- Public API IDs are app-owned. ADK session IDs may be stored in
  `diagnostic_session_id` only as an internal archive reference.
- Every accepted turn and emitted event is persisted before it is relied on for
  replay.
- Event `sequence` is monotonically increasing within one repair session.
- Diagnostic artifacts must be owned by the authenticated user and associated
  with the target repair session before a turn may reference them.
- Safety flags in report envelopes and event payloads use the OpenAPI
  `SafetyFlag` schema exactly.
- Error responses use the OpenAPI `ErrorResponse` envelope.
