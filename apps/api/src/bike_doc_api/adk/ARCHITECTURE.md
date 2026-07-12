# ADK Module Architecture

`bike_doc_api.adk` is the backend's internal Google ADK boundary. It turns an
already-accepted diagnostic turn into an agent run, exposes a deliberately
small service-backed tool catalog to that run, and translates ADK activity
into app-owned events and durable product state. It is not an HTTP service,
does not define the public API, and must not leak prompts, raw ADK events,
model configuration, tool traces, or raw ADK session IDs beyond this package.

Read this document before changing the agent graph, tool catalog, runner event
contract, session lifecycle, or background execution wiring. For public
workflow and persistence rules, see the service-wide
[`apps/api/ARCHITECTURE.md`](../../../ARCHITECTURE.md) and the canonical
specifications linked below.

## Quick map

| Area | Owns | Key entry points |
| --- | --- | --- |
| `background.py` | Process-independent background-task composition and safe setup failure handling | `execute_diagnostic_turn_background`, `_build_background_orchestrator` |
| `orchestration.py` | Accepted-turn processing, seed-context assembly, and mapping normalized runner events to product events/status | `DiagnosticTurnOrchestrator` |
| `runner.py` | Google ADK `Runner` adaptation and raw-event normalization | `DiagnosticRunner`, `DiagnosticRunnerRequest`, `DiagnosticRunnerEvent` |
| `sessions.py` | Mapping app-owned phase sessions to opaque ADK sessions | `DiagnosticPhaseSessionManager`, `DiagnosticADKSessionClient` |
| `agents/` | Phase-specific agent construction and versioned prompt loading | `create_diagnostic_agent`, `create_planning_agent` |
| `prompts/` | Versioned model instructions, separate from orchestration code | `diagnostic.md`, `planning.md`, `execution.md` |
| `tools/` | Internal tool input validation, context checks, service adaptation, and ADK `FunctionTool` wrappers | `build_tool_catalog`, individual tool classes |
| `report_schemas/` | Agent-facing structured report payload models | `DiagnosticReportToolPayload`, `PlanReportToolPayload` |

The production entry point is `execute_diagnostic_turn_background`. The turns
route accepts and durably commits a turn before scheduling this function with
only `user_id`, `repair_session_id`, and `turn_id`. The function validates
runtime configuration, opens a fresh async database session, reloads those
records, and builds a fresh repository/service/agent/orchestrator graph. It
must never retain a request-scoped `AsyncSession`, ORM instance, service, or
FastAPI dependency after the response is returned.

`background.py` imports selected provider and session factories from
`api.deps`; this is a composition-root convenience, not a transport
dependency for ordinary ADK code. The rest of this package must not import
routes, request objects, or public endpoint handlers.

## Dependency direction and boundaries

The package sits behind the service workflow rather than beside the HTTP API:

```text
turn route -> TurnService accepts and commits -> BackgroundTasks
                                             -> adk/background
                                             -> DiagnosticTurnOrchestrator
                                             -> DiagnosticRunner -> Google ADK
                                                                  -> FunctionTools
FunctionTools -> adk tool adapters -> services/providers -> repositories/models/db
DiagnosticRunner -> app-owned runner events -> orchestrator -> EventService/SSE log
```

The arrows are intentional. Routes do not instantiate agents or runners.
Tools do not issue SQL, construct repositories, call routes, or decide public
HTTP status codes. The runner does not persist public events. The orchestrator
does not execute an ADK tool a second time. Services remain authoritative for
authorization, state transitions, report/safety validation, provider
degradation, and durable writes.

The outward-facing ADK seam consists of `DiagnosticRunnerRequest`, the
`DiagnosticRunnerProtocol`, and the typed `DiagnosticRunnerEvent` union. They
contain only app-owned IDs and public-safe data. `DiagnosticRunner` may use
`google.adk` and `google.genai` internally, but its caller receives normalized
assistant deltas/completions, requested input, safety escalation, report
completion, referenced artifacts, and recoverable errors—not an ADK event or
session object.

## Diagnostic turn lifecycle

The diagnostic path is asynchronous so model latency does not extend the HTTP
request:

```text
accepted turn and turn.started committed
  -> background reloads user, turn, and repair session
  -> orchestrator resolves app phase session and builds server-owned context
  -> profile/history/artifact tools seed a DiagnosticRunnerRequest
  -> runner streams normalized ADK events
  -> orchestrator persists user-visible events and derives terminal status
  -> turn.completed is persisted with the repair-session snapshot
```

`DiagnosticTurnOrchestrator.process_turn` first snapshots scalar user and turn
fields so later commits or rollbacks cannot leave it using expired ORM state.
It loads the diagnostic `RepairPhaseSession`, constructs a
`DiagnosticToolContext`, and invokes read tools to seed bike profile, relevant
repair history, and approved diagnostic-artifact metadata. Artifact references
attached to the accepted turn are emitted before model output. The resulting
`DiagnosticRunnerRequest` carries the app-owned diagnostic phase-session ID,
the opaque stored ADK session ID, the user message, allowed artifact IDs, and
the seeded context.

While the runner is active, assistant delta and completed-message events are
validated and appended through `EventService`; that service persists before
local SSE fan-out. Input-request, safety, and report notifications update the
orchestrator's terminal-state tracking only, because their tools already made
the authoritative service write during ADK function calling. A completed
report normally moves the repair session to `awaiting_decision`; an input
request leaves it awaiting the user; active blocking safety can move it to
`blocked_safety`.
Recoverable errors are appended as public error events and finish in a safe,
retryable awaiting-user state. A cancellation is re-raised. Unexpected
orchestration failure follows the same safe error/completion path where
possible; there is no automatic whole-turn retry.

Background setup failure follows a similar rule. It restores a verified,
owned repair session out of `running`, writes a retryable
`diagnostic_processing_error` and terminal event when a turn is known, and
does not disclose missing or unowned records. FastAPI `BackgroundTasks` are
local-first, in-process work—not a durable job queue—so worker crashes and
restarts require a future queue boundary without changing the public turn/SSE
contract.

## Agents, prompts, and runner

`agents/diagnostic.py` constructs the real `google.adk.agents.Agent` named
`diagnostic_agent`. It loads `prompts/diagnostic.md`, uses the configured
diagnostic model, and receives exactly the V1 diagnostic tool catalog. Prompt
files are versioned, readable instruction assets; they guide behavior but do
not enforce product policy. Server services enforce safety, ownership, phase,
and report rules independently.

`agents/planning.py` is a separate planning construction seam with a planning
prompt and one price-lookup tool. It is scaffolding for a future planning
workflow, not part of the active background diagnostic turn path. Its model
selection currently uses the diagnostic-agent model setting. `agents/execution.py`
and `report_schemas/execution.py` are placeholders. Similarly,
`tools/repair_reference.py` and the package-level `artifacts.py` are reserved
seams, not implemented integrations. Do not treat their presence as a shipped
execution or reference-retrieval capability.

`DiagnosticRunner` owns the direct ADK adapter. Given a real agent and the
shared ADK session service, it creates `google.adk.runners.Runner` and passes
the user message plus a `state_delta` whose `app_context` contains only
server-seeded app data. It streams `Runner.run_async(...)` incrementally. Text
is coalesced before persistence at a 25-character threshold or 150 ms flush
interval, then emitted as `DiagnosticRunnerAssistantDelta`; remaining text is
flushed before one `DiagnosticRunnerAssistantMessageCompleted` event. Function
responses are interpreted into typed input-request, safety, report, or error
notifications. The convenience `run()` method merely collects `stream()` for
compatibility; production orchestration consumes `stream()`.

The runner supports an injected invoker and runner factory for isolated tests.
It converts unexpected runtime failures to a public-safe,
retryable `DiagnosticRunnerRecoverableError`, while allowing cancellation to
propagate. It must preserve events that were already streamed and persisted.
Raw model metadata, prompt content, provider details, and arbitrary ADK state
must not be added to the normalized union.

## Sessions and tool catalog

`sessions.py` separates product phase-session identity from ADK runtime state.
`DiagnosticPhaseSessionManager` creates or resumes one
`RepairPhaseSession` per repair session and phase. The row's ID is the
app-owned `diagnostic_session_id` that may appear in a report; its
`adk_session_id` is internal and opaque. Creation races are handled by rolling
back, best-effort deleting the orphaned ADK session, and returning the row
that won the unique database race.

The current `DiagnosticADKSessionClient` uses one process-lifetime
`InMemorySessionService`, with fixed internal ADK app/user names. The exact
same instance must create sessions and run turns; creating one per request or
runner would orphan persisted raw IDs. On process restart, an existing phase
row can point to unavailable memory. `ensure_adk_session_available` turns that
condition into the recoverable `diagnostic_session_unavailable` event; silently
recreating the session is forbidden because it would lose conversation state.
This is suitable for local, single-worker use only. Multi-worker deployment
needs sticky routing or a durable ADK session backend.

`tools/common.py` defines the internal, strict `DiagnosticToolContext`, common
success/error envelope, input parsing, and mapping of known `AppError`
failures. Every catalog wrapper extracts `app_context` from ADK `ToolContext`
state and validates it; the model cannot supply or replace user, repair
session, phase, phase-session, or turn identity. Known failures normalize to
codes such as `not_found`, `invalid_phase`, `stale_session`,
`validation_error`, `artifact_not_found`, `report_validation_failed`, and
`safety_policy_violation`.

The live V1 diagnostic catalog in `tools/tool_catalog.py` binds dependencies
once and exposes six ADK `FunctionTool`s:

- `get_bike_profile` and `lookup_repair_history` read owner-scoped repair
  context through `RepairSessionService` protocols.
- `list_diagnostic_artifacts` returns approved metadata through
  `ArtifactService`, never storage paths or signed URLs.
- `request_diagnostic_input` persists a structured follow-up through
  `TurnService`.
- `raise_safety_flag` delegates to `DiagnosticSafetyService`.
- `save_diagnostic_report` delegates to `ReportService` after the
  agent-facing `DiagnosticReportToolPayload` is converted and validated.

The last three mutate product state directly in the ADK tool loop, exactly
once. Their function responses are notifications to the runner and
orchestrator, not commands to repeat a write. `tools/price_lookup.py` is a
planning-only adapter around the cost-estimate service; it is intentionally
not a diagnostic V1 tool.

## Report, test, and change guidance

`report_schemas/diagnostic.py` is an internal structured-output model. It
extends the public diagnostic report shape for agent-tool validation, but it
is not a serialization shortcut: `ReportService` remains responsible for
mapping, artifact ownership checks, safety reconciliation, persistence, and
public-envelope validation. Public reports expose the app-owned phase-session
ID, never `adk_session_id` or tool traces. Planning has an analogous payload
type; execution has no implementation yet.

Keep deterministic coverage under `apps/api/tests/unit/adk/`: tests cover
agent/catalog construction, individual tool adapters, session races and stale
memory, runner normalization/coalescing/error behavior, orchestration event
mapping, and background composition. Use injected protocols, fake services,
fake ADK invokers, or a fake session client—never a live model, database,
storage system, or external provider. Prompt quality and model behavior belong
in `evals/bike-doc`, not API unit tests. Validate any agent graph/tool change
with `agents-cli lint --fix`; use the project dry-run when the agent is usable.

When adding a phase, first establish its app-owned report and service workflow,
then add a narrow context, tools, agent construction, runner events, and
orchestration mapping. Update this document whenever the module's
responsibilities, tool catalog, event boundary, session backend, or dependency
direction materially changes.

## Related documentation

- [API architecture](../../../ARCHITECTURE.md)
- [Backend layer and import rules](../../../../../docs/specs/apps/api.md)
- [ADK diagnostic tool contracts](../../../../../docs/specs/apps/adk-diagnostic-tools.md)
- [ADK wiring specification](../../../../../docs/specs/apps/adk-wiring-spec.md)
- [Diagnostic report schema](../../../../../docs/specs/apps/diagnostic-report-v1.md)
- [Diagnostic safety rules](../../../../../docs/specs/apps/safety-diagnostic.md)
