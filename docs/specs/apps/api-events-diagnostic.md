# Bike Doc Diagnostic Event And SSE Spec

Status: Draft v0.1
Last updated: 2026-06-21

This spec defines diagnostic-slice repair-session event semantics and
server-sent event behavior. It is a companion to `docs/specs/apps/api.md`,
`docs/specs/apps/api-diagnostic.md`, `docs/specs/apps/api-db-diagnostic.md`,
and the public contract in `docs/specs/openapi.yaml`.

The public event stream remains the OpenAPI `RepairSessionEvent` stream. This
spec does not add a second public event taxonomy.

## References

- Backend scaffold: `docs/specs/apps/api.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Diagnostic DB schema: `docs/specs/apps/api-db-diagnostic.md`
- Public API contract: `docs/specs/openapi.yaml`
- Diagnostic roadmap: `docs/specs/apps/diagnostic-implementation-plan.md`

## Scope

This document covers:

- Event sequence allocation for one repair session.
- Public event IDs and SSE replay cursors.
- `after` and `Last-Event-ID` behavior.
- Stream timeout and heartbeat behavior.
- Diagnostic-slice event types.
- Internal-to-public event mapping.
- Persistence timing and V1 replay retention.

It does not define non-diagnostic execution progress behavior. The OpenAPI
`execution.step.updated` event remains part of the broader product contract but
is not emitted by the diagnostic slice.

## Event Identity And Sequence

Every persisted public event has:

- `sequence`: an integer that is monotonically increasing within one
  `repair_session_id`.
- `id`: the public event ID serialized in API payloads and SSE frames.

For V1, `RepairSessionEvent.id` is exactly `sequence` encoded as a decimal
string. The SSE frame `id` field is also `sequence` encoded as a decimal
string.

The database may keep a separate internal primary key such as `evt_...` for
foreign keys and debugging. That internal key is not the OpenAPI event `id`,
must not be exposed as the SSE `id`, and must not be accepted as a public
cursor.

Sequence allocation rules:

- Sequence starts at `1` for the first persisted event in a repair session.
- `0` is reserved as a replay cursor meaning "before the first retained event."
- Sequence is monotonic within a repair session, not globally.
- A sequence number is never reused within the same repair session.
- Event insertion and `repair_sessions.latest_event_sequence` updates happen in
  the same transaction.
- Concurrent event appends for the same repair session are serialized by
  locking the session row before allocating `latest_event_sequence + 1`.

## SSE Wire Format

The diagnostic event endpoint is:

```text
GET /v1/repair-sessions/{sessionId}/events
Accept: text/event-stream
Authorization: Bearer <token>
```

Each public event frame uses the OpenAPI wire format:

```text
id: <event.sequence as decimal string>
event: <RepairSessionEvent.type>
data: <JSON-encoded RepairSessionEvent>
```

Example:

```text
id: 2
event: assistant.delta
data: {"id":"2","session_id":"rs_123","turn_id":"turn_123","type":"assistant.delta","sequence":2,"created_at":"2026-06-21T17:02:01Z","data":{"text":"I need one photo of the rear derailleur."}}
```

`data` is always the full serialized `RepairSessionEvent`, not just the
event-specific `data` object.

## Replay Cursor Semantics

The public SSE cursor is the event sequence number encoded as a decimal string.
Cursor values are interpreted as "the last event the client fully processed."
Replay always returns events with `sequence > cursor_sequence` in ascending
sequence order.

`after` behavior:

| Request cursor | Behavior |
|---|---|
| omitted | Start after the repair session's current `latest_event_sequence`, then wait for live events. |
| `0` | Replay all retained events for the repair session, then wait for live events. |
| known event ID or sequence | Replay retained events strictly newer than that sequence, then wait for live events. |

For V1, "known event ID" and "known sequence" are the same public decimal
string because `RepairSessionEvent.id = sequence::text`.

Invalid cursors:

- Negative numbers are invalid.
- Non-integer strings are invalid.
- Internal event row IDs such as `evt_...` are invalid.
- Values greater than the repair session's current `latest_event_sequence` are
  invalid.

Invalid cursors return `422 Validation Error` with the OpenAPI `ErrorResponse`
envelope and `error.code: validation_error`.

## `Last-Event-ID`

`Last-Event-ID` uses the same public decimal cursor format as `after`.

Resolution order:

1. If the `after` query parameter is present, use `after`.
2. Otherwise, if the `Last-Event-ID` header is present, use `Last-Event-ID`.
3. Otherwise, use the omitted-cursor behavior: start after the current
   `latest_event_sequence`.

This means `after` takes precedence over `Last-Event-ID`, matching
`docs/specs/openapi.yaml`.

## Stream Lifecycle

On a valid request, the server:

1. Authenticates the bearer token.
2. Verifies the repair session exists and is owned by the authenticated user.
3. Resolves the cursor from `after`, `Last-Event-ID`, or current latest event.
4. Sends retained events with `sequence > cursor_sequence` in ascending order.
5. Keeps the connection open for live persisted events until timeout,
   cancellation, or a server error.

If authentication fails, the session is not found, or cursor validation fails,
the server returns the corresponding HTTP error before opening the SSE stream.
Once a `200 OK` stream is open, later recoverable processing failures are sent
as OpenAPI `error` events when possible.

## Stream Timeout

`timeout_seconds` follows `docs/specs/openapi.yaml`:

- Default: `30`
- Minimum: `5`
- Maximum: `120`

When the timeout elapses, the server closes the SSE response cleanly after any
event frame currently being written. Clean timeout is not an `error` event.
Clients should reconnect with the latest processed SSE `id` as either `after`
or `Last-Event-ID`.

The server may close earlier if the client disconnects or the application is
shutting down. Clients must treat a closed stream as a normal reconnect case
unless the HTTP response was a non-`200` error.

## Heartbeats

Diagnostic streams emit OpenAPI `heartbeat` events while the connection is open
and no other event has been sent recently. Heartbeats keep mobile and proxy
connections from going idle and give clients a fresh cursor after reconnect.

V1 heartbeat rules:

- Heartbeat cadence should be less than the configured `timeout_seconds`.
- A heartbeat uses `event: heartbeat`.
- The `data` field is a full `RepairSessionEvent` whose event-specific payload
  is `{"ok":true}`.
- Heartbeats participate in the same sequence as all other session events.
- A heartbeat is persisted before its SSE frame is written.
- Heartbeats do not change repair-session phase, status, reports, safety state,
  current input request, or execution progress.

If an implementation also uses SSE comment keepalives for infrastructure
compatibility, those comments are transport-only and are not OpenAPI events.
Only `event: heartbeat` frames are public `RepairSessionEvent` values.

## Diagnostic Event Types

The diagnostic slice uses these OpenAPI `RepairSessionEventType` values:

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

The diagnostic slice does not emit `execution.step.updated`.

## Event Data Contracts

Each diagnostic event's `data` object must match the corresponding OpenAPI
schema:

| Event type | OpenAPI data schema |
|---|---|
| `turn.started` | `TurnStartedEventData` |
| `assistant.delta` | `AssistantDeltaEventData` |
| `assistant.message.completed` | `AssistantMessageCompletedEventData` |
| `input.requested` | `InputRequestedEventData` |
| `artifact.referenced` | `ArtifactReferencedEventData` |
| `phase.report.created` | `PhaseReportCreatedEventData` |
| `phase.transitioned` | `PhaseTransitionedEventData` |
| `safety.escalated` | `SafetyEscalatedEventData` |
| `turn.completed` | `TurnCompletedEventData` |
| `error` | `ErrorEventData` |
| `heartbeat` | `HeartbeatEventData` |

## Internal-To-Public Mapping

ADK runner callbacks, tool outputs, and orchestration state changes are
internal. They must be mapped into the OpenAPI event types above before
persistence or streaming.

| Internal source | Public diagnostic event |
|---|---|
| User turn accepted for diagnostic processing | `turn.started` |
| Assistant text token or chunk | `assistant.delta` |
| Assistant message finalized | `assistant.message.completed` |
| Agent or service needs more user input | `input.requested` |
| Uploaded artifact becomes part of the diagnostic context | `artifact.referenced` |
| Diagnostic report envelope persisted | `phase.report.created` |
| Product phase changes from diagnostic to another phase | `phase.transitioned` |
| Safety state changes or repair instructions are blocked | `safety.escalated` |
| Turn processing reaches a terminal state | `turn.completed` |
| Recoverable stream-visible processing failure | `error` |
| Idle open stream keepalive | `heartbeat` |

Internal event names, ADK event classes, callback names, tool names, model
provider events, and prompt details are not public API values.

## Persistence Timing

Public events must be durable before clients are expected to rely on them for
replay.

Rules:

- Persist an event before writing its SSE frame whenever possible.
- For multi-event state transitions, persist all events and state changes in a
  single transaction before notifying SSE listeners.
- Never stream a public event only from process memory.
- Never update session state that depends on an event without persisting the
  event in the same transaction.
- Notify live stream listeners only after the transaction commits.

For report completion, event order is:

1. `safety.escalated`, if the report changes safety state.
2. `phase.report.created`.
3. `turn.completed`, when the report completes the producing turn.
4. `phase.transitioned`, if the product flow immediately leaves diagnosis.

For turn acceptance, the `turn.started` event is persisted in the same
transaction as the accepted turn row. `TurnAccepted.start_event_id` is the
public ID of that `turn.started` event.

## Replay Retention For V1

V1 retains all persisted events for the life of the repair session. There is no
time-window or count-based truncation policy in V1.

Operational implications:

- `after=0` replays every retained event for the session.
- If a repair session is deleted by an internal maintenance operation, its
  events are deleted with it.
- If a future version adds event compaction or truncation, that version must
  define a distinct response for cursors older than the retained window before
  clients depend on it.

## Error Behavior Summary

Expected pre-stream HTTP errors:

| Status | Case | Error code |
|---:|---|---|
| `401` | Missing, expired, malformed, or unverifiable bearer token. | `unauthorized` |
| `404` | Repair session does not exist or is not owned by the user. | `not_found` |
| `422` | `after`, `Last-Event-ID`, or `timeout_seconds` is invalid. | `validation_error` |

Recoverable processing failures after the stream opens should be represented as
persisted `error` events. Fatal infrastructure failures may terminate the
connection without an event; clients recover by reconnecting from the last
processed event ID.
