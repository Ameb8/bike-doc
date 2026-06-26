# Wire ADK Implementation Plan

Status: Draft implementation plan v0.1
Last updated: 2026-06-25

This document breaks `docs/specs/apps/adk-wiring-spec.md` into
implementation-sized steps that can be handed to a coding agent one at a time.
It is an execution plan only. If this plan conflicts with
`docs/specs/apps/adk-wiring-spec.md`, the wiring spec wins.

Relevant companion specs:

- `docs/specs/apps/adk-wiring-spec.md`: canonical ADK integration and wiring
  requirements.
- `docs/specs/apps/adk-diagnostic-tools.md`: internal diagnostic tool
  contracts and normalized tool result shape.
- `docs/specs/apps/api-diagnostic.md`: public diagnostic API behavior.
- `docs/specs/apps/api-events-diagnostic.md`: persisted event and SSE
  semantics.
- `docs/specs/apps/api-db-diagnostic.md`: app-owned diagnostic persistence and
  ID rules.
- `docs/specs/apps/diagnostic-report-v1.md`: diagnostic report payload,
  validation, and public/internal model boundary.
- `docs/specs/apps/safety-diagnostic.md`: diagnostic safety policy, when
  safety behavior is touched.
- `docs/specs/openapi.yaml`: canonical public API contract.

## Implementation Approach

Implement this work as a sequence of self-contained changes, not as one large
agent prompt. The work crosses dependency pinning, ADK runtime behavior,
process-lifetime services, database-backed orchestration, streaming event
persistence, and public API behavior. Keeping each step scoped makes it easier
to verify the integration and avoid regressions such as duplicate tool writes,
leaked ADK session IDs, or non-streaming delta persistence.

Each step should be reviewable independently. A step may depend on earlier
steps, but it should not opportunistically implement later steps unless the
later change is required to make the current step compile or pass focused
tests.

## Global Guardrails

- Public API payloads and persisted public events remain app-owned. Do not
  expose raw ADK sessions, raw ADK events, prompts, credentials, model traces,
  tool traces, or internal provider metadata.
- Store raw ADK session IDs only behind the app-owned
  `repair_phase_sessions.adk_session_id` boundary. Public diagnostic report
  payloads use the app-owned `repair_phase_sessions.id` as
  `diagnostic_session_id`.
- ADK tools execute directly inside the ADK runtime loop. State-mutating tools
  write product state exactly once during direct tool execution; orchestration
  treats resulting runner events as notifications.
- Production turn orchestration must consume runner events incrementally and
  append public events immediately. Do not collect the entire run before
  writing `assistant.delta` events.
- `POST /v1/repair-sessions/{sessionId}/turns` accepts a turn, persists
  `turn.started`, schedules background execution with primitive IDs only, and
  returns `202 Accepted`.
- The background task opens a fresh async database session and rebuilds its
  dependencies. It must not reuse request-scoped sessions, repositories,
  service instances, ORM objects, or FastAPI dependency instances.
- Automatic background retries of whole ADK runs are forbidden. Recoverable
  runner failures become persisted `error` events followed by
  `turn.completed`.
- A `turn.completed` event is written at the end of every handled background
  execution, after the repair-session status has been restored to the correct
  terminal or awaiting state.

## Step 1: ADK Compatibility Spike And Dependency Pins

### Explanation

The runner adapter depends on concrete ADK and Gen AI import paths, event
shapes, session APIs, and streaming behavior. This step verifies those APIs
against installed packages before committing dependency versions. Do not rely
on guessed method names from examples in the spec; adapt to the verified
installed package API while preserving the spec's runtime contract.

### Scope

- Install or sync the backend environment with compatible `google-adk` and
  `google-genai` versions.
- Verify exact import paths and signatures for:
  - `google.adk.agents.Agent`
  - `google.adk.tools.FunctionTool`
  - `google.adk.runners.Runner`
  - `Runner.run_async(...)`
  - ADK streamed text, function call, tool response, final response, and usage
    metadata event fields or methods
  - `google.adk.sessions.InMemorySessionService`
- Pin exact compatible versions in `apps/api/pyproject.toml`.
- Update the lock file using the repository's dependency workflow.

### Done

- `apps/api/pyproject.toml` contains exact compatible versions, not broad
  ranges such as `google-adk>=...`.
- The lock file reflects the pinned versions.
- The implementer has captured the verified ADK event inspection approach
  inside the code or tests that will normalize events later, rather than
  relying on unverified placeholder methods.

### Testable Conditions

- `task sync` completes.
- A small import/signature smoke check can instantiate or reference the ADK
  classes used by later steps.
- Existing backend tests still import the application without dependency
  resolution errors.

## Step 2: Agents CLI Manifest

### Explanation

The canonical wiring spec requires the repository to be recognizable by
`agents-cli`, but the application remains a FastAPI backend with app-owned
public APIs. This step adds project metadata and points eval paths at the repo
locations chosen by the spec.

### Scope

- Run `agents-cli scaffold enhance . --prototype` from the repository root.
- Add or update `agents-cli-manifest.yaml`.
- Configure evaluations and datasets to use `evals/bike-doc` rather than the
  default `tests/eval/`.
- Avoid modifying unrelated backend runtime code in this step.

### Done

- `agents-cli-manifest.yaml` exists at the repository root.
- `agents-cli info` recognizes the repository.
- Manifest eval and dataset paths match the canonical wiring spec.

### Testable Conditions

- `agents-cli info` reports the project metadata without path errors.
- `agents-cli lint` can start far enough to read project metadata, even if
  later code-facing lint failures are deferred to implementation steps.

## Step 3: Process-Lifetime ADK Session Service And Runtime Configuration

### Explanation

`InMemorySessionService` is stateful. Creating it per request or per runner
would orphan persisted ADK session IDs and break background execution. This
step introduces the real ADK session client and wires the session service as a
process-lifetime dependency before any route starts relying on it.

### Scope

- Replace the placeholder local ADK session client with a real client backed
  by `InMemorySessionService`.
- Add a singleton, lifespan-owned, or equivalent process-lifetime provider for
  the session service.
- Ensure the same session service instance is used by:
  - session creation
  - `DiagnosticRunner`
  - any adapter that resumes ADK sessions
- Add or confirm settings for:
  - `DIAGNOSTIC_LLM_PROVIDER`
  - `DIAGNOSTIC_AGENT_MODEL`
  - `DIAGNOSTIC_AGENT_TEMPERATURE`
  - `DIAGNOSTIC_AGENT_MAX_OUTPUT_TOKENS`
  - `DIAGNOSTIC_AGENT_TIMEOUT_SECONDS`
  - provider-specific Google AI or Vertex AI environment requirements
- Add startup or first-run validation for coherent provider/model/credential
  configuration.
- Implement stale in-memory ADK session handling as a recoverable runner error
  path, not silent recreation, unless the verified ADK runtime documents safe
  recreation semantics.

### Done

- A diagnostic phase session stores the app-owned phase-session row and raw
  ADK session ID according to the database and wiring specs.
- The raw ADK session ID remains internal and is never serialized through
  public API schemas or events.
- Session service lifecycle tests prove that the client and runner receive the
  same process-lifetime instance.
- Invalid provider or generation settings fail fast before accepting work that
  would only fail later in a background task.

### Testable Conditions

- Unit tests for `adk/sessions.py` create and reuse an ADK session through the
  shared service provider.
- A test or smoke check proves two dependency resolutions do not create two
  unrelated `InMemorySessionService` instances for the same process.
- Configuration validation rejects an invalid provider or missing required
  provider settings.

## Step 4: ADK Tool Catalog And Diagnostic Agent Construction

### Explanation

The diagnostic agent should become a real ADK `Agent` with a narrow internal
tool surface. Tool wrappers translate ADK tool context into
`DiagnosticToolContext`, expose only model-controllable parameters, and call
existing backend tool/service logic with dependencies bound by the server.

### Scope

- Update `apps/api/src/bike_doc_api/adk/tools/tool_catalog.py` to return ADK
  `FunctionTool` wrappers around the V1 diagnostic tools:
  - `get_bike_profile`
  - `lookup_repair_history`
  - `list_diagnostic_artifacts`
  - `request_diagnostic_input`
  - `raise_safety_flag`
  - `save_diagnostic_report`
- Ensure model-controllable fields are the only function parameters exposed to
  ADK. Server-owned fields come from `tool_context.state["app_context"]`.
- Validate ADK context into `DiagnosticToolContext`.
- Normalize known domain failures into the common result shape from
  `adk-diagnostic-tools.md`.
- Ensure state-mutating tools perform authoritative writes exactly once during
  direct tool execution.
- Update `apps/api/src/bike_doc_api/adk/agents/diagnostic.py` to return a real
  `google.adk.agents.Agent` with the configured model, diagnostic prompt, and
  tool catalog.

### Done

- The diagnostic agent factory returns a real ADK `Agent`.
- Tool wrappers do not let the model provide or override `user_id`, `turn_id`,
  `diagnostic_session_id`, ownership data, phase state, or raw ADK session IDs.
- Tool wrappers call existing tool classes or services through bound
  dependencies and do not query SQL directly or call route handlers.
- Existing tool behavior remains covered by focused unit tests.

### Testable Conditions

- Unit tests for `test_diagnostic_agent.py` verify the agent name, model,
  instruction, and registered tool names.
- Tool catalog tests verify server-owned context is required from ADK state and
  model-provided attempts to override server-owned identifiers are ignored or
  rejected.
- State-mutating tool tests prove the tool writes once and returns normalized
  result metadata suitable for runner notifications.
- `agents-cli lint` validates prompt structures, tool signatures, and parameter
  typing; it is not used for graph/workflow validation.

## Step 5: Streaming ADK Runner Boundary

### Explanation

The runner is the adapter between ADK runtime events and app-owned diagnostic
runner events. It must stream incrementally so orchestration can persist
`assistant.delta` events while `Runner.run_async(...)` is still active. This
step is the main protection against accidentally implementing a non-streaming
collector.

### Scope

- Add the preferred streaming contract:
  `DiagnosticRunner.stream(request) -> AsyncIterator[DiagnosticRunnerEvent]`.
- Keep any existing `run(...) -> DiagnosticRunnerResult` only as a documented
  compatibility collector around `stream(...)`.
- Instantiate ADK `Runner` with the real diagnostic agent and the shared
  session service.
- Seed ADK session state with server-owned app context before running the turn.
- Convert the user message into the verified `google.genai.types.Content`
  shape for the installed SDK version.
- Iterate `Runner.run_async(...)` directly.
- Normalize verified ADK events into app-owned runner events:
  - coalesced `DiagnosticRunnerAssistantDelta`
  - `DiagnosticRunnerAssistantMessageCompleted`
  - `DiagnosticRunnerInputRequested`
  - `DiagnosticRunnerReportCompleted`
  - `DiagnosticRunnerSafetyEscalated`
  - `DiagnosticRunnerRecoverableError`
- Coalesce text deltas by 25 characters or 150ms, whichever happens first.
- Flush remaining text before yielding the completed assistant message.
- Catch runner-level non-fatal exceptions and emit a recoverable error with
  `code="diagnostic_processing_error"` and `retryable=True`.
- Never yield raw ADK events, raw ADK session IDs, prompts, credentials, model
  metadata, or tool traces.

### Done

- Production orchestration can consume runner events as they arrive.
- `run(...)`, if retained, is clearly non-production collector behavior and is
  tested as a wrapper over `stream(...)`.
- Runner exceptions after earlier deltas preserve incremental semantics:
  already-yielded deltas are not erased or rewritten.
- Terminal structural notifications carry only public-safe metadata.

### Testable Conditions

- A fake ADK async stream test proves a delta is yielded before a later final
  response event.
- Coalescing tests prove flush by character threshold and flush by elapsed time.
- A final-response test proves remaining buffered text is flushed before
  `assistant.message.completed`.
- An exception test proves a recoverable error is yielded after any successful
  prior deltas.

## Step 6: Orchestration Consumes Runner Stream Incrementally

### Explanation

The orchestrator is responsible for persisting app-owned public events and
updating product state. It should not understand raw ADK details and should not
rerun tools based on runner notifications. Its key job in this step is to
append each streamed event immediately through the existing event service.

### Scope

- Update `apps/api/src/bike_doc_api/adk/orchestration.py` so production turn
  processing calls `self.runner.stream(...)`.
- Build `DiagnosticRunnerRequest` with app-owned IDs, turn text, artifact IDs,
  seeded bike profile, repair history, and diagnostic artifacts.
- Call `_process_runner_event(...)` inside the `async for` loop.
- Append `assistant.delta` events as soon as they are received.
- Treat `DiagnosticRunnerInputRequested`,
  `DiagnosticRunnerReportCompleted`, and `DiagnosticRunnerSafetyEscalated` as
  notifications of already-performed direct ADK tool execution.
- Ensure the orchestrator does not call `SaveDiagnosticReportTool.run(...)`,
  `RaiseSafetyFlagTool.run(...)`, or request-input persistence a second time
  in response to those notifications.
- Append public `error` events for `DiagnosticRunnerRecoverableError`.
- Restore session status according to the mapping in `adk-wiring-spec.md`
  before appending `turn.completed`.

### Done

- `EventService.append_event(...)` is invoked during runner iteration, not
  after collecting a full runner result.
- Public event ordering follows `api-events-diagnostic.md`, especially around
  report completion and safety escalation.
- Every handled background execution path writes terminal `turn.completed`.
- Repair-session status is no longer `running` before the `turn.completed`
  payload snapshot is validated and written.

### Testable Conditions

- Unit tests prove `assistant.delta` is appended before
  `DiagnosticRunnerAssistantMessageCompleted` is emitted by the fake runner.
- Tests prove report and safety notifications do not trigger duplicate
  state-mutating tool calls.
- Recoverable runner error tests prove the event order is:
  prior successful events, `error`, `turn.completed`.
- Status mapping tests cover at least input request, report completion, safety
  escalation, and recoverable error paths.

## Step 7: Turn Acceptance Route And Background Execution

### Explanation

The public turn route should accept work quickly and move the slow LLM/tool run
out of the request lifecycle. The route owns validation, idempotency, initial
event persistence, and scheduling. The background task owns fresh dependency
construction and orchestration.

### Scope

- Update `apps/api/src/bike_doc_api/api/v1/turns.py` to accept FastAPI
  `BackgroundTasks`.
- Ensure the route calls a service method that accepts the turn, validates
  idempotency, sets the repair session to `running`, persists
  `turn.started`, commits, and returns `TurnAccepted`.
- Add `background_tasks.add_task(...)` with primitive identifiers only:
  `user_id`, `repair_session_id`, and `turn_id`.
- Implement the background entrypoint so it:
  - opens a fresh async database session
  - rebuilds repositories, services, tool dependencies, agent, runner, and
    orchestrator
  - reloads the user and accepted turn by ID
  - calls `orchestrator.process_turn(...)`
  - handles missing user/turn/session cases by restoring session state and
    appending a recoverable error event when possible
- Preserve idempotency behavior:
  - same `client_turn_id` and same request hash returns original
    `TurnAccepted`
  - same `client_turn_id` and different request hash returns conflict
  - new non-idempotent turns are rejected while the session is `running`

### Done

- `POST /v1/repair-sessions/{sessionId}/turns` returns `202 Accepted` after
  turn acceptance and does not call ADK orchestration inline.
- The background task does not receive or retain request-scoped SQLAlchemy
  sessions, repositories, service instances, ORM objects, user models, or
  FastAPI dependency instances.
- Session locking prevents overlapping non-idempotent turns for one repair
  session.
- SSE streaming remains driven by persisted events; no special route-level SSE
  rewrite is introduced.

### Testable Conditions

- API tests prove the turn route returns `202` and schedules background work.
- A focused test proves the route passes only primitive identifiers into the
  background task.
- Idempotency tests cover exact retry, conflicting retry, and new turn while
  `running`.
- A background task test proves dependencies are rebuilt from a fresh session
  and orchestration is invoked with reloaded user/turn data.

## Step 8: SSE And Event Service Verification

### Explanation

The wiring spec expects the existing SSE infrastructure to remain functional.
This step should mostly add proof, not rewrite the event route, because
`EventService.append_event(...)` already commits and publishes events through
the local broker.

### Scope

- Review `apps/api/src/bike_doc_api/services/events.py` and
  `apps/api/src/bike_doc_api/api/v1/events.py` against
  `api-events-diagnostic.md`.
- Add focused tests proving live stream listeners receive events appended
  during an open stream.
- Confirm persisted replay uses public sequence IDs and not internal
  `evt_...` row IDs.
- Confirm heartbeat behavior still follows the diagnostic event spec.
- Avoid changing SSE route behavior unless tests reveal a mismatch with the
  canonical event spec.

### Done

- Streamed `assistant.delta` delivery depends on persisted events committed via
  `EventService.append_event(...)`.
- Replay and live delivery use the same public `RepairSessionEvent` shape.
- Cursor behavior remains compatible with `after` and `Last-Event-ID` rules.

### Testable Conditions

- Service or API tests prove an event appended while an SSE stream is open is
  yielded without reconnecting.
- Replay tests prove `after=0`, omitted `after`, and known public sequence
  cursors behave as specified.
- Invalid cursor tests continue to return the OpenAPI error envelope with
  `validation_error`.

## Step 9: End-To-End Verification And Cleanup

### Explanation

After the pieces are wired, run a narrow integration pass that proves the
system behaves as a local-first ADK-backed diagnostic turn. This is not a full
product test matrix; it is a confidence pass for the high-risk integration
contracts.

### Scope

- Run focused unit and API tests touched by the integration.
- Run repository-level checks expected by the project.
- Run `agents-cli lint` for prompt/tool signature validation.
- Manually exercise a diagnostic turn when credentials and model access are
  available.
- Remove obsolete stubs only if no tests or local development path still
  depend on them.
- Keep compatibility helpers only when they are documented and tested.

### Done

- `task test` passes, or any remaining failures are unrelated and documented
  with exact failing tests.
- `agents-cli lint` passes for ADK prompt/tool validation, or failures are
  documented with explicit follow-up work.
- A local manual turn returns `202` quickly, writes `turn.started`, streams
  `assistant.delta` through persisted events, writes a terminal structural
  event or recoverable `error`, and writes `turn.completed`.
- No public API response, public event, or diagnostic report exposes raw ADK
  session IDs or raw ADK event/provider metadata.

### Testable Conditions

- Automated checks:
  - `task sync`
  - `task test`
  - `agents-cli lint`
- Focused tests cover:
  - dependency import and session lifecycle
  - tool catalog context isolation
  - runner streaming and coalescing
  - orchestration incremental event persistence
  - turn route background scheduling
  - SSE live delivery of appended events
- Manual check, when credentials are configured:
  - submit `POST /v1/repair-sessions/{sessionId}/turns`
  - verify `202 Accepted` response is returned quickly
  - connect to `GET /v1/repair-sessions/{sessionId}/events`
  - verify deltas arrive before final turn completion
  - verify report or safety writes are not duplicated

## Suggested Coding-Agent Prompt Shape

For each step, give the coding agent:

- the step title from this plan
- the relevant sections of `docs/specs/apps/adk-wiring-spec.md`
- the companion specs listed for that step
- the exact files in scope
- the explicit done criteria
- the focused testable conditions

Avoid prompts that ask for "implement the whole ADK wiring spec." The work is
interdependent enough that large prompts invite broad, hard-to-review diffs and
subtle contract drift.
