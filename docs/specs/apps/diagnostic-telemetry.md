# BikeDoc Diagnostic Telemetry Level 2 Spec

Status: Draft v0.1
Last updated: 2026-08-06

This document defines the first traceable, production-shaped telemetry setup
for BikeDoc's diagnostic-agent workflow. It adds enough information to inspect
one diagnostic turn, reconstruct a multi-turn diagnostic phase session, and
detect operational regressions without capturing private model content or
building an analytics platform.

This is the canonical Level 2 diagnostic telemetry specification. The general
process logging behavior remains governed by `docs/specs/apps/logging-setup.md`.
For a Level 2 deployment, the logger-specific defaults in Section 10.5 refine
that spec's original global local-`DEBUG` default.
Product events remain governed by `docs/specs/apps/api-events-diagnostic.md`.
Diagnostic behavior and semantic-quality evaluations remain governed by
`docs/specs/apps/agent/diagnostic-observation-handling.md`.

## References

- Backend architecture: `apps/api/ARCHITECTURE.md`
- ADK module architecture: `apps/api/src/bike_doc_api/adk/ARCHITECTURE.md`
- Configuration: `docs/specs/apps/config-setup.md`
- Logging: `docs/specs/apps/logging-setup.md`
- Diagnostic API: `docs/specs/apps/api-diagnostic.md`
- Diagnostic events: `docs/specs/apps/api-events-diagnostic.md`
- Diagnostic artifacts: `docs/specs/apps/api-artifacts-diagnostic.md`
- Diagnostic observation handling:
  `docs/specs/apps/agent/diagnostic-observation-handling.md`
- Image diagnosis: `docs/specs/apps/agent/image-diagnosis.md`
- ADK activity logging: `https://adk.dev/observability/logging/`
- ADK tracing: `https://adk.dev/observability/traces/`
- OpenTelemetry Python instrumentation:
  `https://opentelemetry.io/docs/languages/python/instrumentation/`
- OpenTelemetry Python exporters:
  `https://opentelemetry.io/docs/languages/python/exporters/`

## Normative Language

The terms **must**, **must not**, **should**, **should not**, and **may** are
normative. "Must" and "must not" define required behavior. "Should" and
"should not" define the expected default unless a later canonical spec records
a justified exception.

## 1. Decision Summary

Level 2 telemetry consists of four distinct records:

| Record | Purpose | Storage |
|---|---|---|
| Structured application logs | Find lifecycle events, warnings, and failures | Existing stdout log pipeline |
| OpenTelemetry traces | Explain the ordered work and latency within one diagnostic turn | Configured OTLP backend |
| OpenTelemetry metrics | Show aggregate rates, outcomes, and latency distributions | Configured OTLP backend |
| Durable product events | Reconstruct exactly what the client was shown and the workflow state written by the app | Existing PostgreSQL event log |

Each invocation of the diagnostic background execution path must create one
root trace. An accepted turn normally has one background execution attempt, but
the telemetry contract does not provide durable or exactly-once job delivery.
A process crash before background execution starts may therefore leave only the
durable `turn.started` product event and no trace. If a turn is executed more
than once, each attempt has a distinct trace ID while sharing the same app-owned
`turn_id` and `diagnostic_session_id`. Durable execution, retry, and attempt
deduplication are outside Level 2.

No trace remains open while BikeDoc waits for the user. Keeping a span open
across minutes, hours, app exits, or process restarts would produce unreliable
telemetry and couple tracing to product persistence. Session-level behavior is
instead represented by correlated turn traces and one completion summary.

The initial external export protocol is OTLP over HTTP. This keeps the
application independent of a specific trace viewer or cloud vendor. A local
OpenTelemetry-compatible backend, an OpenTelemetry Collector, or a managed
backend may receive the same data. Direct vendor exporters are not required by
this version.

## 2. Goals

- Make a selected diagnostic phase session inspectable from its app-owned ID.
- Show the ordered work performed during each diagnostic turn.
- Distinguish visual-context, seed-context, agent, and finalization latency.
- Show why each turn stopped: more input, report completion, safety handling,
  recoverable failure, or normal runner exhaustion without a terminal action.
- Show how many turns a completed diagnostic phase session required and how
  much wall-clock time elapsed.
- Preserve model/provider, prompt, extraction, and report-schema versions needed
  to compare rollouts.
- Measure input-request, report-completion, validation-failure, safety, and
  runner-error rates using bounded dimensions.
- Keep normal local output readable by suppressing unrelated dependency DEBUG
  logs.
- Ensure telemetry export failure never changes diagnostic product behavior.
- Reuse the existing diagnostic telemetry protocols and deterministic test
  seams where practical.

## 3. Non-Goals

Level 2 does not add:

- prompt-response capture
- assistant-response content capture
- raw ADK event persistence
- chain-of-thought or private reasoning capture
- BigQuery Agent Analytics
- a telemetry warehouse or custom dashboard
- an OpenTelemetry Collector deployment owned by this repository
- production alert routing or on-call policy
- adaptive trace sampling
- a durable diagnostic hypothesis graph or agent state machine
- a new telemetry database table
- a span that remains open while waiting for user input
- automatic semantic grading of production conversations
- tracing of planning, execution, or post-turn profile inference
- automatic HTTP-server instrumentation solely to connect the accepting
  `POST` request to the background turn

The implementation should remain small enough to run locally with an OTLP
viewer and to export unchanged to a future production backend.

## 4. Questions the Telemetry Must Answer

| Question | Authoritative signal |
|---|---|
| What happened during this diagnostic session? | Turn traces and `diagnostic_session_id` correlation |
| How many user turns were required? | `diagnostic_session_completed.turn_count` |
| Why did each turn stop? | Turn `outcome` and `terminal_status` |
| What did the system spend time doing? | Child-span durations |
| Did image preparation prevent agent invocation? | Visual-context span and `visual_context_blocked` outcome |
| Did the model ask for more evidence or save a report? | Terminal-action fields and counters |
| Which ADK tools and model calls ran? | ADK `call_llm` and `execute_tool` spans |
| Did a report fail structural validation? | Validation event and metric by bounded stage |
| Did safety handling occur? | Safety count and final safety fields |
| Was the session slow because of processing or user pauses? | Per-turn processing durations and gaps between turn timestamps |
| Was the diagnosis mechanically correct? | Offline evaluations, not production telemetry |

Telemetry must not claim to answer semantic questions that require labeled
evidence, such as causal-overreach rate or safety-critical miss rate. Those
remain evaluation metrics under `evals/bike-doc`.

## 5. Signal Boundaries

### 5.1 Structured Logs

Logs record a small number of named lifecycle events and actionable failures.
They are optimized for filtering by event name, component, trace ID, and
app-owned session or turn ID.

Logs must not mirror every trace span. Assistant deltas, successful event-row
writes, and every raw ADK event must not produce INFO logs.

### 5.2 Traces

Traces record the ordered work of one diagnostic background turn. They are the
primary tool for answering "what did this run do?" and "where was it slow?"

ADK's own OpenTelemetry spans must use the same global tracer provider as
BikeDoc spans. When ADK runs inside the active BikeDoc agent span, model and
tool spans should appear as descendants rather than as unrelated traces.

### 5.3 Metrics

Metrics record aggregate counts and latency distributions. Metrics must contain
only bounded dimensions. App-owned record IDs must never be metric attributes.

Metrics are emitted through OpenTelemetry instruments, not calculated by
parsing application logs.

### 5.4 Durable Product Events

The existing repair-session event log remains authoritative for user-visible
messages, input requests, report creation, safety escalation, and phase or
status transitions. Telemetry may record that one of those events occurred,
but must not duplicate its content payload.

## 6. Correlation Model

### 6.1 Correlation Unit

The root trace is one background execution attempt for an accepted diagnostic
turn. The required app-owned correlation hierarchy is:

```text
repair_session_id
  -> diagnostic_session_id
       -> turn_id
            -> background execution attempt (one root trace)
```

`diagnostic_session_id` is the primary filter for examining the diagnostic
phase across multiple turns. `turn_id` selects one trace. `repair_session_id`
allows correlation with public product events and reports.

### 6.2 Required Identifiers

The following identifiers are allowed on BikeDoc-owned structured logs and
spans:

- `repair_session_id`
- `diagnostic_session_id`
- `turn_id`
- `trace_id`
- `span_id`

The following identifiers are forbidden from all diagnostic telemetry:

- `user_id`
- email, authentication subject, or display name
- artifact storage paths or signed URLs
- provider request IDs unless a later privacy review explicitly approves them

ADK-owned spans may retain the opaque session or conversation, invocation,
event, and tool-call correlation IDs that the installed ADK adds
automatically. BikeDoc must not copy those ADK-only identifiers onto BikeDoc
spans, structured logs, span events, metrics, or durable product events. This
narrow exception does not permit user identity, provider request IDs, content,
tool arguments or responses, or storage locations. It avoids custom
exporter-level span mutation solely to remove privacy-safe third-party
correlation metadata.

App-owned IDs must not be attached to metrics.

### 6.3 Turn Index

Every diagnostic turn trace and completion log should include `turn_index`, a
one-based index among turns belonging to the current diagnostic phase session.

The value must be computed from durable `RepairTurn` rows associated with the
phase session. Specifically, it is the count of phase-session turns whose
`start_event_sequence` is less than or equal to the current turn's durable
`start_event_sequence`. This produces a stable one-based ordinal even if the
turn is executed again after later turns exist. It must not be computed as the
current total turn count or as an ordinal among all repair-session events,
because later turns and non-turn events would make those values unstable. A
repository count query is sufficient; no new database column is required.

### 6.4 HTTP and Background Boundaries

The accepted-turn HTTP request and the diagnostic background run have different
lifetimes. The initial implementation must create a new root trace at
`execute_diagnostic_turn_background` rather than relying on request context to
survive response completion.

If request tracing is added later, the background root may carry a span link to
the accepting request. Level 2 must not persist trace context or modify the
public turn contract solely for this link. The shared `turn_id` remains the
required correlation mechanism.

### 6.5 Structlog Context

While the background root span is active, BikeDoc must bind the allowed
correlation fields and the current OpenTelemetry `trace_id` and `span_id` using
`structlog.contextvars`. The context must be cleared in a `finally` block.

Background processing must bind its own context. It must not assume that
request middleware context remains active after the HTTP response.

## 7. Trace Model

### 7.1 Span Hierarchy

The required minimum span tree is:

```text
bike_doc.diagnostic.turn
  ├─ bike_doc.diagnostic.visual_context.prepare
  ├─ bike_doc.diagnostic.seed_context.build
  │   ├─ bike_doc.diagnostic.seed.get_bike_profile
  │   ├─ bike_doc.diagnostic.seed.lookup_repair_history
  │   └─ bike_doc.diagnostic.seed.list_artifacts
  ├─ bike_doc.diagnostic.agent.run
  │   ├─ ADK call_llm / generate_content spans
  │   └─ ADK execute_tool spans
  └─ bike_doc.diagnostic.turn.finalize
```

The exact internal ADK span names are owned by the installed ADK version and
must not be copied into BikeDoc metric or log contracts. BikeDoc tests should
verify parentage and presence, not hard-code every ADK implementation detail.

### 7.2 Root Turn Span

`bike_doc.diagnostic.turn` starts at entry to the background function, before
runtime validation and record reload, and ends after `turn.completed` is
committed or after the best-effort setup-failure path finishes.

Required root attributes available at start:

| Attribute | Type | Notes |
|---|---|---|
| `bike_doc.repair_session.id` | string | App-owned ID |
| `bike_doc.turn.id` | string | App-owned ID |
| `bike_doc.workflow.phase` | string | Always `diagnostic` |
| `bike_doc.telemetry.schema_version` | string | `diagnostic_telemetry.v1` |

Required attributes added after durable records are loaded:

| Attribute | Type |
|---|---|
| `bike_doc.diagnostic_session.id` | string |
| `bike_doc.turn.index` | integer |
| `bike_doc.report.schema_version` | string |
| `bike_doc.agent.provider` | string |
| `bike_doc.agent.model` | string |
| `bike_doc.agent.prompt_version` | string |
| `bike_doc.image_analysis.mode` | string |
| `bike_doc.input.artifact_count` | integer |
| `bike_doc.input.has_text` | boolean |

Required final attributes:

| Attribute | Type |
|---|---|
| `bike_doc.turn.outcome` | bounded string |
| `bike_doc.turn.terminal_status` | bounded string |
| `bike_doc.turn.duration_ms` | integer |
| `bike_doc.turn.time_to_first_output_ms` | integer, omitted if no assistant output |
| `bike_doc.output.delta_count` | integer |
| `bike_doc.output.message_count` | integer |
| `bike_doc.terminal_action_count` | integer |
| `bike_doc.safety.escalated` | boolean |

Report-completed turns also add the bounded and numeric report fields defined in
Section 10.3. Raw report fields must not be attached.

### 7.3 Visual-Context Span

`bike_doc.diagnostic.visual_context.prepare` covers `prepare_turn(...)`,
including artifact loading, permitted preprocessing, and observation extraction
performed by that service.

Allowed attributes:

- `bike_doc.visual.invoke_agent`
- `bike_doc.visual.current_image_count`
- `bike_doc.visual.current_observation_count`
- `bike_doc.visual.prior_observation_count`
- `bike_doc.visual.status_usable_count`
- `bike_doc.visual.status_limited_count`
- `bike_doc.visual.status_unusable_count`
- `bike_doc.visual.recoverable_error_count`
- bounded extractor/provider/model/version fields already approved by the image
  observation telemetry contract

Artifact IDs, observation text, extractor explanations, raw scores, and image
content are prohibited.

### 7.4 Seed-Context Spans

`bike_doc.diagnostic.seed_context.build` covers server-owned context assembly.
Its three child spans show whether bike-profile, repair-history, or artifact
metadata lookup caused delay or failed recoverably.

Allowed result attributes are limited to:

- success boolean
- returned item count
- bounded error code

Bike profile values, repair-history text, artifact metadata, tool request
arguments, and returned payloads must not be attached.

### 7.5 Agent-Run Span

`bike_doc.diagnostic.agent.run` begins immediately before
`DiagnosticRunner.stream(...)` and ends when the stream is exhausted or raises.
The ADK runner call must execute while this span is current so that automatic
model and tool spans are nested beneath it.

BikeDoc may update the span with counts derived from normalized app-owned runner
events. It must not add raw ADK events, assistant text, function arguments,
function responses, model prompts, or completion-basis rationale.

### 7.6 Finalization Span

`bike_doc.diagnostic.turn.finalize` covers terminal status derivation and the
final `turn.completed` persistence. It must not create a child span for every
assistant delta or database event append.

### 7.7 Span Events

Only the following BikeDoc span events are required:

- `diagnostic.terminal_action`
- `diagnostic.report_validation_failed`
- `diagnostic.safety_escalated`
- `diagnostic.recoverable_error`

Span events use the same safe bounded fields as structured logs. They must not
contain exception messages from providers when those messages could include
request or response data. Recorded exceptions may include stack traces for
BikeDoc code, but the exception message must be privacy-reviewed or replaced by
a bounded error class.

### 7.8 Span Status

- Successful input request, report completion, and safety-blocked outcomes are
  valid product outcomes and must not set OpenTelemetry error status.
- A report validation rejection that the agent corrects during the same run is
  a span event, not a failed root trace.
- A runner or persistence exception that prevents the intended turn operation
  sets error status on the affected span and root span.
- Cancellation re-raises. It records `outcome: cancelled` when no
  higher-precedence durable product result completed; it is not rewritten as a
  recoverable error.
- Normal runner exhaustion with no terminal tool action records
  `outcome: no_terminal_action` and a warning log, whether or not the runner
  produced assistant output. It does not automatically become an
  infrastructure error.

## 8. Turn Outcomes

`outcome` is the single overarching result of the turn. It summarizes the
turn's primary durable product result; it does not replace or attempt to encode
every tool call, span event, safety occurrence, validation failure, or error
that happened during the turn.

Exactly one final `outcome` must be selected for each root turn span and
`diagnostic_turn_completed` log:

| Outcome | Meaning |
|---|---|
| `input_requested` | The turn ended by successfully requesting user input. |
| `report_completed` | The turn ended by successfully saving a diagnostic report. |
| `visual_context_blocked` | Visual preparation intentionally prevented agent invocation and returned the session to the user. |
| `recoverable_error` | A retryable runner or setup failure was surfaced safely. |
| `terminal_error` | A non-retryable backend failure left the turn failed. |
| `no_terminal_action` | The runner ended normally without a committed input request or report. |
| `cancelled` | Task cancellation propagated. |

The orchestrator must collect the relevant facts while the turn runs and apply
the following precedence once, when the root span closes:

1. `report_completed` when a diagnostic report was durably committed during
   the turn.
2. `input_requested` when no report completed and an input request was durably
   committed during the turn.
3. `visual_context_blocked` when neither terminal action completed and visual
   preparation intentionally prevented agent invocation.
4. `cancelled` when no preceding product result completed and cancellation
   propagated.
5. `terminal_error` when no preceding product result completed and a
   non-retryable failure prevented the intended turn operation.
6. `recoverable_error` when no preceding product result completed and a
   retryable failure was surfaced safely.
7. `no_terminal_action` otherwise.

A terminal action counts for outcome selection only after its existing
database transaction commits successfully. An attempted or rejected tool call,
an uncommitted write, or a telemetry event alone does not establish a terminal
outcome. A failure or cancellation after a terminal action has committed does
not replace that durable product result; it is represented separately by span
status, span events, error fields, and logs as applicable.

When no higher-precedence product result completed, error outcomes are
classified by the resulting product state rather than by the Python exception
type alone:

- `recoverable_error` requires BikeDoc to durably append a bounded recoverable
  error, safely finalize the turn, and leave the session in a valid state that
  can accept another turn.
- `terminal_error` applies when BikeDoc cannot safely finalize the turn or
  cannot establish a valid state from which the user can continue. Invalid
  runtime configuration, missing or inconsistent accepted-turn records, and a
  persistence failure that prevents error or turn-completion recording are
  terminal for that execution, even if an operator could later repair the
  underlying cause.

Safety escalation is an orthogonal fact. A turn may raise a safety flag and
then request input or save a report. The final `terminal_status` records whether
the resulting session is `blocked_safety`.

The turn telemetry state must count state-mutating terminal actions. More than
one input-request or report-completion action in one turn, or both actions in
one turn, emits a `diagnostic_turn_multiple_terminal_actions` warning and adds
`bike_doc.terminal_action_count` to the trace. Telemetry does not itself undo or
repeat product writes.

## 9. Session Reconstruction and Summary

### 9.1 Per-Session Inspection

To inspect one diagnostic phase session, an operator filters logs and traces by
`diagnostic_session_id`. Ordered root spans show each accepted turn. The gap
between one completed turn and the next turn start represents time outside
agent processing, usually waiting for user input.

The implementation must not manufacture a separate session trace containing
links to every turn. Grouping by the app-owned session ID is sufficient for
Level 2.

### 9.2 Session Completion Summary

The execution that successfully creates and commits a report must make a
best-effort emission of one `diagnostic_session_completed` structured log and
the two session histograms in Section 11. An execution that merely observes an
already-created report must not emit them again.

This telemetry is normally emitted once but does not provide exactly-once
delivery across process crashes. A crash after report commit may omit the
summary, while failure recovery may produce duplicate exported telemetry. The
durable report and product event records remain authoritative when exact
reconstruction is required. Level 2 does not add a transactional outbox,
telemetry delivery marker, or deduplication state.

Required summary fields:

| Field | Definition |
|---|---|
| `repair_session_id` | App-owned repair-session ID |
| `diagnostic_session_id` | App-owned phase-session ID |
| `completion_reason` | Validated V2 completion reason |
| `turn_count` | Count of diagnostic turns for this phase session, including the completing turn |
| `session_elapsed_ms` | Report completion time minus phase-session `created_at` |
| `single_turn_completion` | `true` only when `turn_count == 1` |
| `report_schema_version` | Snapshotted diagnostic report version |

This summary needs one count query over turns for the phase session and the
existing phase-session creation timestamp. It does not require a new table,
column, or long-lived in-memory accumulator.

### 9.3 First-Finding Same-Turn Metric

`same_turn_completion_after_first_finding` must not be computed from
`observed_finding_count > 0`. Every valid V2 report has at least one observed
finding, so that expression does not identify the turn in which the first
finding appeared.

Level 2 records `single_turn_completion` as a separate, accurately measurable
fact. It does not claim that this is equivalent to completion in the same turn
as the first finding.

The canonical first-finding metric remains unavailable until BikeDoc has a
privacy-safe, structured, durable definition of the first communicated
diagnostic finding. Adding a checkpoint tool or persisted hypothesis state
solely to obtain this metric is explicitly out of scope.

## 10. Structured Logging Contract

### 10.1 Logging Prerequisite

The `structlog` setup in `docs/specs/apps/logging-setup.md` must be completed
before Level 2 telemetry is considered usable. Structured keyword fields must
appear in console and JSON output; storing them only in stdlib `LogRecord.extra`
while omitting them from the formatter does not satisfy this spec.

Application code must use `structlog.get_logger(__name__)`. ADK and third-party
stdlib logs must continue to flow through the common renderer.

### 10.2 Common Fields

Every diagnostic lifecycle log must contain:

- `event`
- `event_family: diagnostic_flow`
- `component`
- `environment`
- `telemetry_schema_version: diagnostic_telemetry.v1`
- `repair_session_id`
- `turn_id`
- `trace_id`
- `span_id`

`diagnostic_session_id` and `turn_index` are required after the phase-session
row is loaded. An early setup failure may omit them.

Other identifiers or metadata that could not be established during early setup
must likewise be omitted from logs and spans rather than fabricated.

### 10.3 Required Events

#### `diagnostic_turn_started`

Level: `INFO`

Emitted once after the turn, repair session, user ownership, and phase session
have been verified.

Additional safe fields:

- `diagnostic_session_id`
- `turn_index`
- `provider`
- `model`
- `prompt_version`
- `report_schema_version`
- `image_analysis_mode`
- `artifact_count`
- `has_text_input`
- `responds_to_input_request` as a boolean, never the request ID

#### `diagnostic_turn_completed`

Level: `INFO` for normal outcomes, `WARNING` for `recoverable_error` or
`no_terminal_action`, and `ERROR` for `terminal_error`.

Emitted once for every background turn that reaches a terminal path.

Additional safe fields:

- `diagnostic_session_id`
- `turn_index`
- `outcome`
- `terminal_status`
- `duration_ms`
- `agent_run_duration_ms`, omitted when the agent was not invoked
- `time_to_first_output_ms`, omitted when there was no assistant output
- `artifact_count`
- `current_image_count`
- `current_observation_count`
- `prior_observation_count`
- `assistant_delta_count`
- `assistant_message_count`
- `terminal_action_count`
- `input_request_type`, only when input was requested
- `input_required`, only when input was requested
- `safety_escalated`
- `safety_state`
- `report_completed`
- `completion_reason`, only when a V2 report completed
- `observed_finding_count`, only when a report completed
- `contributing_factor_count`, only when a report completed
- `alternate_hypothesis_count`, only when a report completed
- `error_code`, only for bounded application errors
- provider/model/prompt/report/extractor version fields

#### `diagnostic_session_completed`

Level: `INFO`

Best-effort emission by the report-creating execution as defined in Section
9.2.

#### `diagnostic_report_validation_failed`

Level: `INFO`

Emitted once per rejected save attempt. It contains:

- `validation_stage`
- `error_code: report_validation_failed`
- `attempt_number` within the current turn
- `report_schema_version`

It must not contain Pydantic validation text, field values, the report, the
completion basis, or `why_ready`. Field paths may be logged only if they are
mapped to a bounded allowlist; arbitrary paths are prohibited.

#### `diagnostic_turn_multiple_terminal_actions`

Level: `WARNING`

Contains only action counts and bounded action kinds.

### 10.4 Validation Stages

Allowed `validation_stage` values are:

- `completion_basis`
- `report_schema`
- `artifact_reference`
- `phase_state`
- `tool_input`
- `unknown`

If the backend cannot safely classify a failure, it uses `unknown` rather than
adding a new high-cardinality string.

### 10.5 Logger Levels and Filtering

The local root logger should default to `INFO`, not global `DEBUG`, for the
diagnostic workflow. A diagnostic-only override may enable DEBUG for BikeDoc
agent modules without enabling dependency noise.

Required default levels:

| Logger | Default level |
|---|---|
| root | `INFO` |
| `bike_doc_api` | `INFO` |
| `bike_doc_api.adk` | configured diagnostic level |
| `bike_doc_api.services.diagnostic_visual_context` | configured diagnostic level |
| `google_adk.google.adk.models.google_llm` | `WARNING` |
| `google_genai` | `WARNING` |
| `httpx` | `WARNING` |
| `httpcore` | `WARNING` |
| `urllib3` | `WARNING` |
| `PIL` | `WARNING` |
| `sqlalchemy.engine` | `WARNING` |

Enabling diagnostic DEBUG must not lower the ADK model logger or HTTP-client
loggers. Raw model logging requires a separate, explicit future privacy policy
and is not part of Level 2.

## 11. Metrics Contract

All metric instruments are created once per process. Duration histograms record
seconds, while log and span duration fields remain milliseconds. Histograms use
these fixed explicit boundaries so expected values do not collapse into the SDK
default overflow or broad count buckets:

| Histogram family | Boundaries |
|---|---|
| Turn duration and time to first output, seconds | `0.1`, `0.25`, `0.5`, `1`, `2.5`, `5`, `10`, `20`, `30`, `60`, `120`, `300` |
| Session elapsed, seconds | `1`, `5`, `15`, `30`, `60`, `120`, `300`, `600`, `1800`, `3600`, `14400`, `86400` |
| Turn and report-item counts | `0`, `1`, `2`, `3`, `4`, `5`, `10`, `20` |

Values above the highest boundary remain valid overflow observations. Changing
these boundaries later is telemetry-backend tuning and does not change product
behavior.

Early setup failures must still record the applicable turn and error metrics.
When a required metric dimension such as provider, model, prompt version,
report-schema version, or image-analysis mode could not be established, the
implementation uses the bounded value `unknown`. It must not invent an
identifier or use `unknown` to normalize an unexpected outcome or other value
whose contract is already bounded. The one exception is
`terminal_status: unknown`, which is allowed only when `outcome` is
`terminal_error` and the durable repair-session state could not be loaded. A
loaded but unexpected terminal status remains an implementation error.

### 11.1 Turn Metrics

| Instrument | Type | Unit | Required attributes |
|---|---|---|---|
| `bike_doc.diagnostic.turn.count` | Counter | `{turn}` | `outcome`, `terminal_status`, `provider`, `model`, `prompt_version`, `report_schema_version`, `image_analysis_mode` |
| `bike_doc.diagnostic.turn.duration` | Histogram | `s` | `outcome`, `provider`, `model`, `prompt_version`, `image_analysis_mode` |
| `bike_doc.diagnostic.turn.time_to_first_output` | Histogram | `s` | `provider`, `model`, `prompt_version`, `image_analysis_mode` |

Time to first output is the monotonic elapsed time from the root turn span's
start at background execution entry to the first normalized assistant delta or
completed assistant message. This includes BikeDoc-controlled preparation
before agent invocation; the agent and model child spans expose the internal
agent portion separately. Turns with no assistant output do not record a
sample.

### 11.2 Outcome Metrics

| Instrument | Type | Unit | Required attributes |
|---|---|---|---|
| `bike_doc.diagnostic.input_request.count` | Counter | `{request}` | `request_type`, `required`, `report_schema_version` |
| `bike_doc.diagnostic.report.count` | Counter | `{report}` | `completion_reason`, `report_schema_version`, `provider`, `model`, `prompt_version` |
| `bike_doc.diagnostic.report.item_count` | Histogram | `{item}` | `item_type`, `completion_reason`, `report_schema_version` |
| `bike_doc.diagnostic.report_validation_failure.count` | Counter | `{failure}` | `validation_stage`, `report_schema_version` |
| `bike_doc.diagnostic.safety_escalation.count` | Counter | `{escalation}` | `severity`, `safety_state`, bounded `flag_code` |
| `bike_doc.diagnostic.runner_error.count` | Counter | `{error}` | `error_code`, `retryable`, `provider`, `model` |

`request_type`, `severity`, `safety_state`, `flag_code`, and `error_code` must
come from existing enums or explicit allowlists.

Allowed `item_type` values are `observed_finding`, `contributing_factor`, and
`alternate_hypothesis`. A completed report records one sample for each type.

### 11.3 Session Metrics

| Instrument | Type | Unit | Required attributes |
|---|---|---|---|
| `bike_doc.diagnostic.session.turns_to_completion` | Histogram | `{turn}` | `completion_reason`, `report_schema_version` |
| `bike_doc.diagnostic.session.elapsed` | Histogram | `s` | `completion_reason`, `report_schema_version` |

No session metric includes `repair_session_id`, `diagnostic_session_id`, or
`turn_id`.

### 11.4 Existing Image Telemetry

Existing observation-extraction metrics remain valid. Implementation should
route them through the same OpenTelemetry meter while preserving their current
privacy allowlists. This spec does not require renaming those metrics during the
first rollout.

Profile-inference telemetry runs after diagnostic processing and is not part of
the diagnostic turn trace. It must not delay root-turn span completion.

### 11.5 ADK-Owned Metrics

The installed ADK may emit its own OpenTelemetry metrics through the shared
global meter provider, including agent or tool duration, workflow-step,
request/response-size, and token-usage instruments. These third-party
operational metrics may be exported alongside BikeDoc metrics, but their names
and exact shape are not part of BikeDoc's stable telemetry contract.

ADK-owned metrics must satisfy the same prohibition on content, user identity,
app-owned IDs, and unbounded dimensions. Tests should verify that boundary
without hard-coding every ADK metric name. Level 2 does not add a filtering
layer for otherwise privacy-safe ADK metrics; filtering may be introduced later
only for a demonstrated privacy, cardinality, or cost problem.

### 11.6 Metrics Interpretation

The following interpretations are forbidden:

- more turns are inherently better
- more findings or hypotheses are inherently better
- same-turn or single-turn completion is inherently better
- fewer input requests always means a higher-quality diagnosis
- a completed report is proof of diagnostic correctness

Metrics identify changes and candidates for evaluation. They do not replace
the labeled behavioral metrics in the diagnostic observation-handling spec.

## 12. Privacy and Cardinality

### 12.1 Prohibited Content

The following must never be represented in ordinary Level 2 logs, traces,
span events, metrics, or exporter-error records:

- user message text
- assistant response text or deltas
- system instructions or prompt body
- model request or response bodies
- ADK conversation history or state
- tool arguments or response payloads
- input-request prompt text or multiple-choice labels
- report summaries, diagnoses, findings, uncertainties, or evidence text
- completion-basis `why_ready`
- hypothesis labels from `material_hypotheses_considered`
- image bytes, EXIF data, OCR text, or storage locations
- bike profile values or repair-history content
- arbitrary provider exception messages
- authentication data or user identity

### 12.2 Message-Content Capture

ADK and OpenTelemetry GenAI message-content capture must remain disabled.
Level 2 must not add an application setting that enables it.

Every diagnostic runner invocation must explicitly use ADK's code-owned
no-content telemetry mode. The implementation must also validate the installed
ADK/OpenTelemetry version's effective content-capture configuration at startup.
If an environment variable, administrative override, runtime hook, or optional
instrumentation package would override that mode or enable either legacy ADK
span content or OpenTelemetry GenAI content capture, a non-test process must
fail startup with a configuration error rather than silently exporting private
content. The spec defines the logical policy, not an unstable third-party
literal value.

An integration test must pass recognizable sentinel content through both model
and tool activity and assert that the sentinel is absent from every exported
span attribute and telemetry log. Checking configuration values alone does not
satisfy this privacy requirement.

### 12.3 Cardinality Rules

- App-owned IDs are allowed only on logs and spans.
- Metric dimensions must use enums, booleans, controlled versions, configured
  provider/model names, or bounded non-negative numbers recorded as values.
- Exception strings, arbitrary field paths, component descriptions, prompts,
  and provider response codes must not become metric attributes.
- A new metric dimension requires an explicit cardinality and privacy review.
- Numeric counts belong in metric values or span/log fields, not encoded in
  dimension names.

### 12.4 Data Retention

Level 2 does not prescribe backend retention because no backend has been
selected. Before enabling OTLP export outside local development, the deployment
owner must document retention and access controls for the selected backend.
The data remains metadata-only even when short retention is configured.

## 13. Configuration

Add these typed fields to `bike_doc_api.core.config.Settings` and document them
in the root `.env.example`:

| Field | Environment variable | Default | Allowed values / rule |
|---|---|---|---|
| `telemetry_exporter` | `BIKE_DOC_API_TELEMETRY_EXPORTER` | `none` | `none`, `otlp` |
| `telemetry_otlp_endpoint` | `BIKE_DOC_API_TELEMETRY_OTLP_ENDPOINT` | null | Required absolute HTTP(S) endpoint without URL user information, query, or fragment when exporter is `otlp`; forbidden otherwise |
| `telemetry_service_name` | `BIKE_DOC_API_TELEMETRY_SERVICE_NAME` | `bike-doc-api` | Non-blank bounded string |
| `diagnostic_log_level` | `BIKE_DOC_API_DIAGNOSTIC_LOG_LEVEL` | `INFO` | Valid stdlib level name |

No separate trace and metric exporter settings are introduced. OTLP tracing
and metrics share the configured base endpoint. The implementation derives the
signal-specific HTTP endpoints required by the exporter.

When `telemetry_exporter` is `none`, structured diagnostic logs remain enabled,
while the OpenTelemetry APIs use no-op providers. This is the default for unit
tests and installations without a trace backend.

When `telemetry_exporter` is `otlp`, both traces and metrics must be exported.
Partial configuration that exports only one signal is invalid for Level 2.

This version intentionally has no settings for sampling, content capture,
custom headers, batch sizes, metric intervals, or vendor-specific project IDs.
Those settings may be introduced only after a deployment need appears.

The diagnostic agent module must expose a code-owned
`DIAGNOSTIC_PROMPT_VERSION` constant. The initial value should match the
accepted diagnostic-observation evaluation baseline, currently
`diagnostic-observation.v1`. This is version metadata, not a deployment knob;
it changes when the diagnostic prompt's semantic contract changes.

## 14. Dependencies

Because BikeDoc directly imports OpenTelemetry APIs and SDK types, it must
declare direct runtime dependencies rather than relying on Google ADK's
transitive dependencies:

```toml
dependencies = [
  "opentelemetry-api>=1.42",
  "opentelemetry-sdk>=1.42",
  "opentelemetry-exporter-otlp-proto-http>=1.42",
  "structlog>=25",
]
```

The exact resolved versions belong in `uv.lock`. Version lower bounds must be
reconciled with the pinned `google-adk` dependency during implementation. Do
not add FastAPI auto-instrumentation, a Collector SDK, BigQuery, or a vendor
agent package for this scope.

## 15. Runtime Initialization and Export

### 15.1 Ownership

`bike_doc_api.core.telemetry` owns process-level tracer and meter provider
configuration. Feature code obtains tracers and meters through OpenTelemetry's
public API; it must not construct exporters.

The FastAPI lifespan starts the telemetry runtime before any background agent
run can be scheduled and flushes it during shutdown. Initialization must be
idempotent enough for app-factory tests. Tests using the `none` exporter must
not install or replace process-global providers.

BikeDoc must configure one global provider set. It must not initialize an app
provider and then separately call an ADK helper that installs another provider,
because duplicate providers can split or duplicate ADK spans.

### 15.2 Resource Attributes

The telemetry resource contains only:

- `service.name` from `telemetry_service_name`
- `service.version` from the BikeDoc API package version
- `deployment.environment.name` from `Settings.environment`

User, repair-session, diagnostic-session, and turn IDs are span/log attributes,
not resource attributes.

### 15.3 Processing

- Traces use a batch span processor.
- Metrics use a periodic exporting metric reader.
- Export must happen outside the diagnostic coroutine's critical path.
- The initial enabled configuration samples all diagnostic turn traces.
- Export queues use SDK defaults unless measurement demonstrates a need to
  tune them.
- Shutdown flush is best-effort and bounded to five seconds.

### 15.4 Failure Behavior

Telemetry initialization failure with `telemetry_exporter: otlp` is a startup
configuration error. After successful startup, an exporter outage must not
fail, retry, delay, or roll back a diagnostic turn.

Runtime exporter failures use the OpenTelemetry SDK's warning and drop
behavior. Those warnings flow through the common log renderer at `WARNING` or
higher. BikeDoc must not wrap the exporter solely to create another event and
must not add a durable retry queue for telemetry.

## 16. Application Integration

### 16.1 Expected File Placement

```text
apps/api/src/bike_doc_api/core/telemetry.py
apps/api/src/bike_doc_api/core/logging.py
apps/api/src/bike_doc_api/core/config.py
apps/api/src/bike_doc_api/main.py
apps/api/src/bike_doc_api/adk/background.py
apps/api/src/bike_doc_api/adk/orchestration.py
apps/api/src/bike_doc_api/services/diagnostic_completion_telemetry.py
apps/api/src/bike_doc_api/services/diagnostic_visual_context.py
apps/api/src/bike_doc_api/adk/tools/reports.py
apps/api/src/bike_doc_api/repositories/repair_sessions.py
apps/api/tests/unit/core/test_telemetry.py
apps/api/tests/unit/adk/test_orchestration.py
apps/api/tests/unit/services/test_diagnostic_completion_telemetry.py
```

The implementation may extend the existing
`diagnostic_completion_telemetry.py` protocol rather than add a second
diagnostic telemetry abstraction. Its recording test adapter should remain the
deterministic way to assert domain measurements.

### 16.2 Background Composition

`execute_diagnostic_turn_background` owns the root span and correlation
context. It passes normal app-owned records into the orchestrator; it must not
add OpenTelemetry context to the public runner request or ADK tool schemas.

### 16.3 Orchestration

`DiagnosticTurnOrchestrator` owns child spans around its app-owned workflow and
maintains the small per-turn telemetry accumulator:

- monotonic start time
- first assistant output time
- normalized runner-event counts
- terminal-action counts and final outcome
- safety occurrence and final safety state
- report composition counts and completion reason

This accumulator is process-local and exists only for the duration of one
turn. It is not domain state and must not be persisted.

### 16.4 Runner Boundary

The app-owned `DiagnosticRunnerEvent` union remains public-safe and must not be
expanded with raw model metadata merely for telemetry. ADK's own spans provide
model and tool timing. App configuration supplies provider/model/prompt version
attributes.

### 16.5 Tool Validation

`save_diagnostic_report` records validation stage through the injected
diagnostic telemetry protocol. The tool still returns the existing concise
agent-facing error envelope. Telemetry does not change retry or validation
behavior.

### 16.6 Session Summary Query

`RepairTurnRepository` should support counts scoped by
`repair_phase_session_id`: a total count for the completing report's session
summary and a count through the current turn's `start_event_sequence` for its
stable `turn_index`. The completing report path combines the total count with
`RepairPhaseSession.created_at`. A general analytics repository, materialized
view, or telemetry table is not required.

## 17. Testing

### 17.1 Configuration Tests

Tests must verify:

- `none` accepts no endpoint
- `otlp` requires a valid absolute HTTP(S) endpoint
- OTLP endpoints containing user information, a query, or a fragment are rejected
- an endpoint is rejected when exporter is `none`
- service name and diagnostic log level validation
- unsafe GenAI content-capture configuration prevents startup
- administrative or runtime overrides cannot defeat the runner's code-owned
  no-content mode

### 17.2 Logging Tests

Tests must verify:

- structured fields are rendered in console and JSON formats
- trace and span IDs appear while a span is current
- background correlation context is cleared after completion
- no user, message, prompt, report, input-request prompt, or `why_ready` content
  appears in required lifecycle logs
- diagnostic DEBUG does not enable `httpcore`, `urllib3`, `PIL`, ADK model, or
  Google GenAI DEBUG logs
- each terminal turn emits exactly one `diagnostic_turn_completed`

### 17.3 Trace Tests

Use an in-memory span exporter; do not require a network backend.

Tests must verify:

- one root span per invocation of the background diagnostic execution path
- required child-span parentage
- app-owned IDs on spans and absence from resource attributes
- opaque ADK correlation IDs remain confined to ADK-owned spans, and `user_id`
  remains absent from all exported telemetry
- input-request, report-completion, visual-blocked, error, and cancellation
  outcomes
- recovered validation failures do not mark the root span failed
- a runner failure records error status without exposing its private message
- sentinel model content, tool arguments, and tool responses are absent from
  every exported span attribute and telemetry log
- assistant deltas do not create one span each
- profile inference is not nested beneath the diagnostic turn

A provider-free integration test should prove that ADK spans created under the
global provider inherit the current agent-run span. It should assert the
presence of model/tool child spans at a stable boundary and avoid snapshotting
all third-party span attributes.

### 17.4 Metrics Tests

Use an in-memory metric reader or the existing recording telemetry adapter.

Tests must verify:

- exact increments for each bounded outcome
- duration and time-to-first-output measurements use a monotonic fake clock
- session turn count and elapsed measurements
- no IDs or arbitrary text appear in metric attributes
- ADK-owned metrics contain no content, user identity, app-owned IDs, or
  unbounded attributes without snapshotting their complete instrument set
- validation stages and error codes reject unknown values or normalize them to
  `unknown`
- `terminal_status: unknown` is emitted only for a terminal setup failure that
  could not load durable repair-session state
- one report and one session completion measurement when the current execution
  successfully creates a report, and none when it only observes an existing
  report
- no false `same_turn_completion_after_first_finding` measurement is emitted

### 17.5 Existing Verification

Implementation must run:

```bash
task format
task check
agents-cli lint
```

Because the change configures ADK's telemetry environment, a local dry-run
should also verify that the installed ADK version produces nested spans without
capturing content.

## 18. Manual Verification

Use an OTLP-compatible local viewer or Collector debug exporter. Then complete
a two-turn diagnostic session in which the first turn requests input and the
second saves a report.

Verify:

1. Exactly two `bike_doc.diagnostic.turn` root traces exist.
2. Both traces share `diagnostic_session_id` and have distinct `turn_id` values.
3. `turn_index` is `1` and `2` respectively.
4. The first trace outcome is `input_requested`.
5. The second trace outcome is `report_completed`.
6. Visual preparation, seed context, agent run, and finalization appear in the
   expected order.
7. ADK model and tool spans are nested under the agent-run span.
8. The second turn emits `diagnostic_session_completed` with `turn_count: 2`.
9. Metrics record two turns, one input request, one report, and session
   turns-to-completion of two.
10. Searching exported telemetry reveals no user text, assistant text, prompt,
    report content, tool arguments, image content, or completion rationale.
11. Stopping the OTLP receiver after startup does not prevent the diagnostic
    turn from completing.

Also exercise a report-validation failure followed by a corrected save in the
same agent run. The trace must show the validation event, the validation metric
must increment, and the final root span must remain a successful
`report_completed` outcome.

## 19. Rollout Plan

Implement and enable Level 2 in this order:

1. Complete the structured logging foundation and dependency logger filters.
2. Add typed telemetry settings and the no-op/OTLP runtime.
3. Add the root turn span and log correlation context.
4. Add the four required BikeDoc child-span areas.
5. Extend the existing diagnostic telemetry protocol with bounded metrics.
6. Add accurate session count/elapsed summary measurement.
7. Remove or stop emitting the inaccurate first-finding same-turn field.
8. Verify locally with content capture disabled.
9. Enable OTLP export in a development environment.
10. Select production retention, access, and backend configuration separately
    before enabling production export.

Existing privacy-safe lifecycle events may coexist during implementation, but
`diagnostic_turn_completed` becomes the canonical per-turn summary. Metrics or
dashboards must not double-count both the legacy event and the new summary.

## 20. Acceptance Criteria

- One diagnostic background execution attempt produces one correlated root
  trace; retries for the same turn produce distinct traces sharing `turn_id`.
- A multi-turn diagnostic phase session is inspectable by
  `diagnostic_session_id` without querying private content.
- The trace shows visual context, seed context, agent execution, ADK model/tool
  activity, and finalization latency.
- Every terminal turn emits exactly one structured completion summary.
- When diagnostic-session completion telemetry is emitted, it records accurate
  turn count and elapsed time using existing persistence timestamps; durable
  report and product event records remain authoritative.
- Input requests, reports, validation failures, safety escalations, runner
  errors, turn durations, and session durations are available as bounded
  metrics.
- App-owned IDs appear only in logs and spans, never metric dimensions.
- User and model content is not representable at the approved telemetry
  boundaries.
- ADK message-content capture is disabled and unsafe configuration fails
  startup.
- Normal local logging does not include dependency DEBUG noise or raw ADK model
  dumps.
- Telemetry export is batched and never awaited by diagnostic product logic.
- Export failure after startup does not change report, safety, event, or session
  behavior.
- No telemetry table, long-lived session span, analytics warehouse, vendor
  agent, or new agent state machine is introduced.
- The inaccurate `observed_finding_count > 0` proxy is not reported as
  same-turn completion after the first finding.
