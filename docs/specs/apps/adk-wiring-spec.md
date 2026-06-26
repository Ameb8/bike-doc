# Google ADK Integration and Wiring Spec

Status: Proposed Spec v0.1
Last updated: 2026-06-25

This specification details the steps required to wire up the actual Google ADK library into the Bike Doc backend FastAPI service. It replaces the current stubs and mocks with a production-ready, local-first ADK agent execution model that integrates cleanly with the database, events, and API boundaries.

---

## 1. Guiding Architectural Decisions

To ensure performance, data integrity, and strict separation of concerns, the implementation must adhere to these five architectural constraints:

### A. Asynchronous Turn Execution via FastAPI `BackgroundTasks`
*   **The Constraint**: Generative model requests (LLM latency) and database tool interactions can easily exceed 5–15 seconds, making synchronous execution within the HTTP request lifecycle impractical.
*   **The Design**: The turn acceptance route `POST /v1/repair-sessions/{sessionId}/turns` must accept the user turn, validate idempotency, write the `turn.started` event, and queue the ADK runner execution asynchronously using FastAPI's native `BackgroundTasks`. The HTTP handler then immediately returns a `202 Accepted` response.
*   **Dependency Lifecycle Decision**: The background task must not reuse request-scoped SQLAlchemy sessions, repositories, service objects, ORM models, or FastAPI dependency instances after the HTTP response has been sent. The route may pass only primitive identifiers into the background task, such as `user_id`, `repair_session_id`, and `turn_id`. The background task must open a fresh async database session, rebuild the repository/service/orchestrator graph, reload the current user and turn, and then run orchestration.
*   **Durability Tradeoff**: FastAPI `BackgroundTasks` are intentionally accepted for this local-first implementation because they avoid blocking the HTTP request while keeping the architecture simple. They are in-process and are not durable across worker crashes or process restarts. A future production queue may replace this boundary without changing the public turn/SSE contract.
*   **Benefits**: Avoids holding HTTP connection sockets open during model inference, improves client response times, and requires no heavy external message broker (e.g. Celery) for local development.

### B. Direct Tool Execution with Post-Processing Notifications
*   **The Constraint**: Google ADK models require real-time feedback from tools (such as checking if a report succeeded or loading history) to determine their next reasoning step. This is done via **Automatic Function Calling (AFC)**.
*   **The Design**: ADK tools must execute natively *inside* the model's runtime execution loop (direct execution). When the runner yields a terminal event like `DiagnosticRunnerReportCompleted` or `DiagnosticRunnerSafetyEscalated`, the orchestrator treats these as **post-processing notifications** rather than execution triggers. 
*   **Persistence Decision**: ADK tools that mutate product state are responsible for performing the authoritative write exactly once during direct tool execution. In V1 this applies to `request_diagnostic_input`, `raise_safety_flag`, and `save_diagnostic_report`. The runner may yield normalized app-owned events after those tools complete, but those events must contain already-persisted result metadata and must not cause the orchestrator to invoke the same tool a second time.
*   **Notification Payload Decision**: Terminal runner notifications should carry the minimum public-safe metadata needed for event publication or flow control, such as `report_id`, report summary, report schema version, safety state, tool error code, or retryability. They must not carry raw ADK tool traces, raw ADK session state, prompts, credentials, or internal model metadata.
*   **Benefits**: Prevents double-persistence bugs (saving the report/safety flags twice) while keeping the model updated on tool execution status.

### C. Time and Character Coalescing for SSE Deltas
*   **The Constraint**: Writing a database row for every single streamed token/delta from the LLM introduces massive IO bottlenecking and clutters the database.
*   **The Design**: The runner adapter must buffer incoming LLM text deltas only long enough to coalesce them. It must emit each combined `DiagnosticRunnerAssistantDelta` to the orchestrator while `Runner.run_async(...)` is still active whenever the buffer exceeds **25 characters** or **150ms** has elapsed since the last flush. The orchestrator must append each emitted delta to the public event log immediately, allowing the existing SSE broker to deliver it to listeners in real time. The final completed message is stored once as `assistant.message.completed`.
*   **Non-Goal**: Do not collect all runner events in memory and return them only after the ADK turn finishes. That implementation shape satisfies replay persistence but does **not** provide real-time user-visible streaming.
*   **Benefits**: Reduces database writes by ~90% while maintaining a highly responsive streaming user interface over Server-Sent Events.

### D. Opaque Session Isolation
*   **The Constraint**: Internal ADK session details or state schemas must never leak through public API contracts or events.
*   **The Design**: The database stores the raw ADK session ID in `repair_phase_sessions.adk_session_id`. The public API only exposes the app-owned `repair_phase_sessions.id` (with prefix `phs_...`) as the `diagnostic_session_id`.
*   **ADK Session Service Lifecycle Decision**: The ADK `SessionService` must be process-lifetime state, created once during application startup or through an equivalent singleton dependency. The ADK session client and runner must receive the same `SessionService` instance so that a phase session created during turn acceptance can be resumed by background execution. Creating a new `InMemorySessionService` per request or per runner invocation is forbidden because it would orphan previously created ADK session IDs.
*   **Scaling Tradeoff**: `InMemorySessionService` is acceptable for local-first V1 and single-worker development. It is not a durable multi-worker session backend. Production multi-worker deployment must either use sticky routing plus process-local constraints or move to a durable ADK session backend before enabling multiple workers.
*   **Benefits**: Ensures that changing ADK storage backends (e.g., swapping from in-memory to cloud vector stores) has zero impact on mobile clients.

### E. Precise CLI Integration (Correcting Graph Validation Inaccuracies)
*   **The Constraint**: The diagnostic agent is designed as a standard LLM-driven chat `Agent`, not a graph-based `Workflow`.
*   **The Design**: Static ADK 2.0 graph routing or state-transition checks do not apply to this agent. Instead, `agents-cli lint` must be used solely to validate prompt structures, tool signatures, and parameter typing.

---

## 2. Implementation Steps

### Step 1: Backend Dependencies
Add the required packages to the backend dependencies in [pyproject.toml](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/pyproject.toml):

```toml
[project]
dependencies = [
  ...
  "google-adk==<verified-compatible-version>",
  "google-genai==<verified-compatible-version>",
]
```

The first implementation pass must perform a short compatibility spike before
committing these versions:

1. Install the current ADK and Gen AI packages in the backend environment.
2. Verify the exact import paths and runtime signatures for:
    *   `google.adk.agents.Agent`
    *   `google.adk.tools.FunctionTool`
    *   `google.adk.runners.Runner`
    *   `Runner.run_async(...)`
    *   ADK event fields/methods used for streamed text, function calls, tool
        responses, final response detection, and usage metadata.
    *   `google.adk.sessions.InMemorySessionService`
3. Pin the exact compatible versions in `pyproject.toml`.

Do not leave broad unbounded lower ranges like `google-adk>=2.0.0` in the
committed backend dependency list. The runner adapter depends on ADK event
shapes and session APIs, so silent package upgrades can break streaming or
tool normalization.

Run the package sync command:
```bash
task sync
```

### Step 2: Initialize Agents CLI Project Manifest
Generate the project metadata file `agents-cli-manifest.yaml` in the repository root by running:
```bash
agents-cli scaffold enhance . --prototype
```
Update `agents-cli-manifest.yaml` to point the evaluations and datasets folders to `evals/bike-doc` instead of the default `tests/eval/`. Verify recognition via:
```bash
agents-cli info
```

### Step 3: Implement the Tool Catalog
Update [tool_catalog.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/adk/tools/tool_catalog.py) to wrap each custom tool class (like [GetBikeProfileTool](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/adk/tools/bike_profile.py)) using ADK's `FunctionTool` class:

```python
from google.adk.tools import FunctionTool
from bike_doc_api.adk.tools.bike_profile import GetBikeProfileTool
from bike_doc_api.adk.tools.common import DiagnosticToolContext

def get_bike_profile_wrapper(repair_session_id: str, tool_context: Any) -> dict[str, Any]:
    """Expose bike profile context to the agent."""
    # Maps ADK's tool_context into the app's DiagnosticToolContext
    context = DiagnosticToolContext.model_validate(tool_context.state["app_context"])
    # Instantiate or invoke the tool using wrapped dependency injection
    ...
```

Tool wrapper rules:

*   Expose only model-controllable fields as function parameters. For example,
    `component_terms` and `limit` may be model-provided for
    `lookup_repair_history`.
*   Server-owned context must come only from ADK `tool_context.state`, under
    `tool_context.state["app_context"]`, and must be validated into
    `DiagnosticToolContext`.
*   The model must never provide or override `user_id`, `turn_id`,
    `diagnostic_session_id`, `active_phase`, ownership information, or raw ADK
    session IDs.
*   Dependencies must be bound when the tool catalog is built. Wrappers should
    call existing tool classes or backend services through those bound
    dependencies, not instantiate repositories directly.
*   Known domain failures must be normalized into the common tool result shape
    from `docs/specs/apps/adk-diagnostic-tools.md`.
*   State-mutating tool wrappers must write product state exactly once during
    direct ADK execution. The orchestrator may publish public events based on
    normalized runner notifications, but it must not rerun the tool.

### Step 4: Instantiate the ADK Agent
Modify [diagnostic.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/adk/agents/diagnostic.py) to return a real `google.adk.agents.Agent` instance:

```python
from google.adk.agents import Agent

def create_diagnostic_agent(
    tool_dependencies: DiagnosticAgentToolDependencies,
    *,
    settings: Settings | None = None,
) -> Agent:
    resolved_settings = settings or get_settings()
    
    # Instantiate tools using wrapped callables
    tools = build_tool_catalog(tool_dependencies)

    return Agent(
        name=DIAGNOSTIC_AGENT_NAME,
        model=resolved_settings.diagnostic_agent_model,
        instruction=DIAGNOMPT,
        tools=tools,
    )
```

### Step 5: Implement Real Session Management
In [sessions.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/adk/sessions.py), replace the placeholder `LocalDiagnosticADKSessionClient` with the ADK runtime session engine:

```python
from google.adk.sessions import InMemorySessionService

class RealDiagnosticADKSessionClient:
    def __init__(self, session_service: InMemorySessionService) -> None:
        self._session_service = session_service

    async def create_session(self, *, repair_session_id: str, phase: RepairSessionPhase) -> str:
        session_id = f"adk_{phase.value}_{generate_prefixed_ulid('sess_')}"
        await self._session_service.create_session(
            app_name="bike_doc", 
            user_id="backend_system", 
            session_id=session_id
        )
        return session_id

    async def close_session(self, *, adk_session_id: str) -> None:
        # Best-effort memory cleanup
        pass
```

The `InMemorySessionService` instance shown above must not be created inside a
per-request dependency. It must be owned by application startup/lifespan or a
module-level singleton provider and injected wherever ADK sessions are created
or used. The same instance must be passed to:

*   `RealDiagnosticADKSessionClient`
*   `DiagnosticRunner`
*   any future ADK adapter that resumes or inspects ADK sessions

This requirement exists because the app persists only the opaque ADK session ID
in `repair_phase_sessions.adk_session_id`; with an in-memory ADK backend, that
ID is meaningful only to the `SessionService` instance that created it.

If a phase-session row already exists but the in-memory ADK session no longer
exists because of process restart, the runner should emit a recoverable
`error` event with `retryable: true`, append `turn.completed`, and restore the
repair session out of `running`. Silent recreation under the same
`adk_session_id` is forbidden unless the ADK runtime explicitly documents that
this is safe and preserves expected conversation state.

### Step 6: Implement the Streaming Runner Boundary
Modify [runner.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/adk/runner.py) to expose ADK output as an async stream of app-owned events.

The runner boundary must support incremental consumption by the orchestrator. It may implement either:

*   `async def stream(self, request: DiagnosticRunnerRequest) -> AsyncIterator[DiagnosticRunnerEvent]`
*   `async def run(self, request: DiagnosticRunnerRequest, emit: Callable[[DiagnosticRunnerEvent], Awaitable[None]]) -> DiagnosticRunnerResult`

The preferred shape is the async iterator because it makes the streaming contract explicit and keeps persistence decisions outside the ADK adapter. The existing `run(...) -> DiagnosticRunnerResult` method may remain as a compatibility helper, but it must be documented and tested as a non-streaming collector wrapper around `stream(...)`, not used by production turn orchestration.

Required runner behavior:

*   Iterate `Runner.run_async(...)` directly and normalize ADK events as they arrive.
*   Coalesce text deltas by **25 characters** or **150ms**, whichever happens first.
*   Yield each coalesced `DiagnosticRunnerAssistantDelta` immediately after it is flushed.
*   Flush any remaining text buffer before yielding `DiagnosticRunnerAssistantMessageCompleted`.
*   Yield terminal structural events such as `DiagnosticRunnerInputRequested`, `DiagnosticRunnerReportCompleted`, `DiagnosticRunnerSafetyEscalated`, and `DiagnosticRunnerRecoverableError` as soon as they are identified.
*   Never yield raw ADK event objects, raw ADK session IDs, prompts, model metadata, or tool traces through this boundary.
*   Catch runner-level exceptions and normalize them into
    `DiagnosticRunnerRecoverableError(code="diagnostic_processing_error",
    retryable=True)` unless the exception is a process-fatal error that should
    be allowed to crash the task.
*   Preserve incremental semantics: an exception after some deltas have already
    been yielded must not erase or rewrite those already-persisted public
    events. The orchestrator must append the error and terminal
    `turn.completed` after the last successful event.

State-mutating ADK tool results must be normalized as notifications, not as
commands for the orchestrator to execute. For example:

*   A successful `save_diagnostic_report` tool call may yield
    `DiagnosticRunnerReportCompleted(report_id=..., summary=...,
    schema_version=...)` after the report service has already persisted the
    report and emitted the authoritative `phase.report.created` event.
*   A successful `raise_safety_flag` tool call may yield
    `DiagnosticRunnerSafetyEscalated(...)` only with already-persisted safety
    metadata needed for flow control or logging.
*   A failed state-mutating tool call should be surfaced as either the tool's
    structured result inside the ADK loop and, when user-visible, a normalized
    `DiagnosticRunnerRecoverableError`. The orchestrator must not retry the
    whole turn automatically.

Example shape:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from google.adk.runners import Runner
from google.genai import types

class DiagnosticRunner:
    def __init__(self, agent: Agent, session_service: Any) -> None:
        self._agent = agent
        self._session_service = session_service

    async def stream(
        self,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[DiagnosticRunnerEvent]:
        # Initialize context state to pass to the tool catalog
        session_state = {
            "app_context": {
                "user_id": request.user_id,
                "user_skill_level": request.user_skill_level,
                "repair_session_id": request.repair_session_id,
                "diagnostic_session_id": request.diagnostic_session_id,
                "turn_id": request.turn_id,
            }
        }
        
        # Populate session state variables
        await self._session_service.update_state(request.adk_session_id, session_state)
        
        runner = Runner(
            agent=self._agent, 
            app_name="bike_doc", 
            session_service=self._session_service
        )
        
        char_buffer = []
        last_flush_time = datetime.now(UTC)
        
        async for adk_event in runner.run_async(
            user_id="backend_system",
            session_id=request.adk_session_id,
            new_message=types.Content(
                role="user", 
                parts=[types.Part.from_text(text=request.message_text)]
            )
        ):
            # 1. Map streamed deltas with character/time coalescing
            if adk_event.is_delta():
                text = adk_event.delta_text()
                char_buffer.append(text)
                elapsed_ms = (
                    datetime.now(UTC) - last_flush_time
                ).total_seconds() * 1000
                
                if len("".join(char_buffer)) >= 25 or elapsed_ms >= 150:
                    flushed_text = "".join(char_buffer)
                    char_buffer.clear()
                    last_flush_time = datetime.now(UTC)
                    yield DiagnosticRunnerAssistantDelta(text=flushed_text)
            
            # 2. Map structural terminal notifications
            elif adk_event.is_final_response():
                if char_buffer:
                    yield DiagnosticRunnerAssistantDelta(text="".join(char_buffer))
                    char_buffer.clear()
                yield DiagnosticRunnerAssistantMessageCompleted(
                    message_id=generate_prefixed_ulid("msg_"),
                    full_text=adk_event.final_text()
                )
```

If the ADK event API does not expose `is_delta()`, `delta_text()`, `is_final_response()`, or `final_text()` exactly as shown, adapt the normalizer to the installed ADK version while preserving the streaming contract above.

### Step 7: Wire Async Invocation in Routes
Modify [turns.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/api/v1/turns.py) to accept `BackgroundTasks` and execute the orchestrator asynchronously:

```python
from fastapi import BackgroundTasks

@router.post(
    "/repair-sessions/{sessionId}/turns",
    response_model=TurnAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_repair_session_turn(
    request: TurnCreate,
    background_tasks: BackgroundTasks,
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[TurnService, Depends(get_turn_service)],
    session_id: Annotated[str, Path(alias="sessionId", min_length=1)],
) -> TurnAccepted:
    # 1. Service accepts turn, writes start event, commits transaction
    accepted = await service.accept_turn(
        current_user=current_user,
        repair_session_id=session_id,
        request=request,
    )
    
    # 2. Queue the heavy ADK processing in the background
    background_tasks.add_task(
        execute_orchestration_background,
        user_id=current_user.id,
        repair_session_id=session_id,
        turn_id=accepted.id,
    )
    
    return accepted
```

Background task implementation requirements:

*   `accept_turn(...)` must return after committing `turn.started`; it must not
    call the orchestrator inline.
*   The background task entrypoint must accept primitive identifiers only. Do
    not pass ORM models, `AsyncSession`, repositories, services, `User`
    objects, or request-scoped dependency instances into `BackgroundTasks`.
*   The background task must open a fresh async DB session using the backend's
    configured session factory, rebuild the repositories/services/orchestrator,
    reload the accepted turn and current user by ID, and then call
    `orchestrator.process_turn(...)`.
*   If the background task cannot load the turn or user, it should append a
    recoverable `error` event when possible and restore the repair session out
    of `running`.
*   Background execution must not rely on the HTTP request staying open. SSE
    streaming is driven exclusively by committed public events being appended
    and published through `EventService.append_event(...)`.

### Step 8: Update Orchestration to Persist Streamed Events Immediately
Modify [orchestration.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/adk/orchestration.py) so production turn processing consumes the runner stream directly:

```python
completed = True

async for event in self.runner.stream(
    DiagnosticRunnerRequest(
        user_id=current_user.id,
        user_skill_level=current_user.skill_level,
        repair_session_id=turn.repair_session_id,
        turn_id=turn.id,
        diagnostic_session_id=phase_session.id,
        adk_session_id=phase_session.adk_session_id,
        message_text=_turn_message_text(turn),
        artifact_ids=_turn_artifact_ids(turn),
        bike_profile=seed.bike_profile,
        repair_history=seed.repair_history,
        diagnostic_artifacts=seed.diagnostic_artifacts,
    ),
):
    await self._process_runner_event(
        context=context,
        turn=turn,
        event=event,
    )

if completed:
    await self._append_turn_completed(
        current_user=current_user,
        repair_session_id=turn.repair_session_id,
        turn_id=turn.id,
    )
```

The orchestrator must not wait for a completed `DiagnosticRunnerResult` before appending `assistant.delta` events. `EventService.append_event(...)` already persists, commits, and publishes events to the local SSE broker; invoking it inside the runner event loop is what makes deltas user-visible before the LLM finishes the whole turn.

The orchestrator must also respect direct ADK tool execution:

*   `DiagnosticRunnerInputRequested` should be treated as a notification that
    the request-input tool already persisted the input request, unless the ADK
    wrapper contract explicitly marks it as not yet persisted.
*   `DiagnosticRunnerReportCompleted` should not call
    `SaveDiagnosticReportTool.run(...)`; the report tool already performed the
    authoritative write inside the ADK loop.
*   `DiagnosticRunnerSafetyEscalated` should not call
    `RaiseSafetyFlagTool.run(...)`; the safety tool already performed the
    authoritative write inside the ADK loop.
*   `DiagnosticRunnerRecoverableError` should append a public `error` event and
    allow the terminal `turn.completed` event to be written.

The current pre-ADK stub shape may keep a compatibility collector
`run(...) -> DiagnosticRunnerResult` for unit tests and non-streaming callers,
but production orchestration must use `stream(...)`.

### Step 9: Keep SSE Delivery Infrastructure Unchanged
No route-level SSE rewrite is required for real-time token delivery. [events.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/services/events.py) already supports live delivery because `append_event(...)` commits each event and publishes it to `_LOCAL_EVENT_BROKER`, while `stream_sse_frames(...)` yields broker events to connected clients.

The required outside-code changes are therefore:

*   [runner.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/adk/runner.py): add the streaming runner contract, coalescing logic, and ADK event normalization.
*   [orchestration.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/adk/orchestration.py): consume runner events incrementally and call `_process_runner_event(...)` inside the async loop.
*   [turns.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/api/v1/turns.py): keep `BackgroundTasks` for asynchronous turn execution, but do not rely on it for streaming semantics.
*   [events.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/src/bike_doc_api/services/events.py): no functional streaming change expected; tests may be added to prove `assistant.delta` events are published as soon as appended.
*   [test_runner.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/tests/unit/adk/test_runner.py) and [test_orchestration.py](file:///Users/pattycrowder/Documents/Alex_Documents/Projects/bike-doc/apps/api/tests/unit/adk/test_orchestration.py): update or add tests proving deltas are yielded and appended before final response completion.

---

## 3. Verification Plan

### Automated boundary tests
*   Run unit tests locally to verify event mapping logic:
    ```bash
    task test
    ```
*   Verify tool contracts and type signatures using CLI:
    ```bash
    agents-cli lint
    ```
*   Add runner tests proving coalesced `DiagnosticRunnerAssistantDelta` events are yielded before the final response event when the fake ADK stream is still open.
*   Add orchestration tests proving `EventService.append_event(...)` is called for an `assistant.delta` before the runner emits `DiagnosticRunnerAssistantMessageCompleted`.
*   Add or preserve SSE service tests proving events appended during an open stream are published to connected listeners without requiring stream reconnection.

### Manual Verification
1. Deploy the database migrations locally (`task run` environment).
2. Execute a turn request via `POST /v1/repair-sessions/{sessionId}/turns`.
3. Verify that the response returns HTTP `202` in <100ms.
4. Open a browser or client listening to the Server-Sent Events endpoint `GET /v1/repair-sessions/{sessionId}/events`.
5. Verify that token deltas stream in real-time, coalesced into blocks of >=25 chars/150ms.
6. Verify that the final `save_diagnostic_report` tool execution updates the database phase status to `awaiting_decision` without triggering double-persist validation crashes.

---

## 4. Operational Configuration Decisions

These guidelines specify the operational limits, retry behaviors, concurrency settings, and observability configurations for ADK agent runs:

### A. Retry and Recovery Policy
*   **The Decision**: Automatic background retries of failed ADK runs are forbidden.
*   **The Rationale**: Since model generation is non-deterministic, retrying an entire turn execution could result in duplicate database writes (e.g. redundant safety flags) or interleaved stream events.
*   **Implementation**: Leverage ADK's `ReflectAndRetryToolPlugin` for inline tool retries. For runner-level exceptions, emit an `error` event with `retryable: true` and a terminal `turn.completed` event, allowing the client user to manually click a "Retry" button.
*   **Streaming Semantics**: If a runner-level exception occurs after one or
    more `assistant.delta` events have already been persisted, keep those
    events in the log. Append the `error` event after the last successful
    streamed event, then append `turn.completed`. Do not attempt to delete,
    rewrite, or regenerate partial output automatically.

### B. Turn Completion Event Semantics
*   **The Decision**: A `turn.completed` event must be emitted at the end of every background turn execution.
*   **The Rationale**: Clients rely on this event to stop typing animations, unlock the input fields, and enable interaction buttons.
*   **Implementation**: Write `turn.completed` immediately after:
    *   An `input.requested` event (awaiting user response).
    *   A `phase.report.created` event (diagnostic completed).
    *   An `error` event (handled run failure).
*   **Status Snapshot Requirement**: The `turn.completed` payload must include
    the current repair-session snapshot after status has been restored from
    `running` to its terminal/awaiting state. The session status must be
    updated before validating and writing the `turn.completed` event data.
*   **Status Mapping**:
    *   After an input request: `awaiting_user`.
    *   After a diagnostic report that does not block repair guidance:
        `awaiting_decision`.
    *   After a safety escalation that blocks repair guidance:
        `blocked_safety`.
    *   After a handled recoverable runner error: `awaiting_user` if the user
        can retry or provide more input; `failed` only for a non-retryable
        terminal backend failure.
    *   After cancellation or future explicit user abort handling: `cancelled`.
*   **Drawback**: A slow model keeps the session in `running` until the
    background execution writes `turn.completed`. This is intentional to
    prevent overlapping LLM turns from interleaving streamed events.

### C. Model & Generation Settings
*   **The Decision**: Lock model parameters for highly predictable structured outputs.
*   **Implementation**:
    *   **Temperature**: `0.0` (or `0.2` if `0.0` is not supported).
    *   **Max Output Tokens**: `2048`.
    *   **Inference Timeout**: `30 seconds`.
    *   **Default Model**: `gemini-2.5-flash` (per setting `diagnostic_agent_model`).
*   **Provider Switching Decision**: Provider selection must be controlled by
    environment/configuration, not source changes. The backend must support at
    least:

    ```text
    DIAGNOSTIC_LLM_PROVIDER=google_ai | vertex_ai
    DIAGNOSTIC_AGENT_MODEL=gemini-2.5-flash
    DIAGNOSTIC_AGENT_TEMPERATURE=0.0
    DIAGNOSTIC_AGENT_MAX_OUTPUT_TOKENS=2048
    DIAGNOSTIC_AGENT_TIMEOUT_SECONDS=30
    ```

    For the standard Google AI / Gemini Developer API path, configure the
    runtime with `GEMINI_API_KEY` or `GOOGLE_API_KEY`, depending on the SDK
    version verified during the compatibility spike.

    For the Vertex AI path, configure the runtime with:

    ```text
    GOOGLE_GENAI_USE_VERTEXAI=true
    GOOGLE_CLOUD_PROJECT=<project-id>
    GOOGLE_CLOUD_LOCATION=<region>
    ```

    The code may translate `DIAGNOSTIC_LLM_PROVIDER` into the SDK-specific
    environment/configuration values at process startup, but switching between
    `google_ai` and `vertex_ai` must not require editing source code.
*   **Startup Validation**: On app startup or first ADK runner construction,
    validate that the configured provider, model, credentials, project/location
    values, generation settings, and streaming APIs are coherent. Fail fast for
    invalid configuration rather than accepting turns that will only fail in the
    background task.
*   **Provider Drawbacks**:
    *   Not every model name or generation parameter is guaranteed to behave
        identically across Google AI and Vertex AI.
    *   Vertex AI requires Google Cloud project, location, IAM, and billing
        setup.
    *   Google AI API-key setup is simpler for local development, but may have
        different quota, governance, and data-handling constraints than Vertex
        AI.
    *   Provider-specific authentication failures may occur only at runtime if
        startup validation is skipped.

### D. Evidence Budgeting (Context Seeding)
*   **The Decision**: Set strict thresholds on prior history and artifact data injected into the ADK context to keep token sizes predictable and reduce latency.
*   **Implementation**:
    *   **Repair History**: Max `5` entries, filtered to include records matching component terms in the current turn.
    *   **Artifact Listings**: Max `10` most recent artifacts attached to the active repair session.
    *   **Conversation History**: Max `10` turns, utilizing ADK's `EventsCompactionConfig` for sliding-window summarization on longer chats.

### E. Concurrency Limits (Session Locking)
*   **The Decision**: Enforce serialized, non-overlapping turn executions per `repair_session_id`.
*   **The Rationale**: Concurrent executions within the same session result in race conditions on DB event sequences and write interleaved outputs.
*   **Implementation**: When accepting a turn, the service sets
    `repair_sessions.status` to `running`. Reject subsequent non-idempotent
    requests for that session with an HTTP `409 Conflict`. Restore the status
    according to the Turn Completion Event Semantics mapping before the
    background task writes `turn.completed`.
*   **Idempotency Exception**: If a client retries the exact same
    `client_turn_id` with the same request hash while the session is already
    `running`, return the original `TurnAccepted` response instead of `409`.
    If the same `client_turn_id` is reused with a different request hash,
    return the existing idempotency conflict error.
*   **Accepted Statuses**: New non-idempotent turns may be accepted only when
    the session is in `created` or `awaiting_user`. Future phases may add
    phase-specific accepted states, but `running` is not an accepting state for
    a new turn.
*   **Streaming Requirement**: The lock is held at the repair-session status
    level for the duration of the background LLM/tool run so only one active
    turn can append streamed deltas for a session at a time.

### F. Telemetry & Observability
*   **The Decision**: Operational metrics must be logged strictly via structured logs to stdout/Cloud Logging; they must never be saved in the database or exposed via the public event stream.
*   **Telemetry Schema**: Log the following fields in JSON format via `structlog`:
    *   `adk_run_duration_seconds` (total turn latency)
    *   `model_inference_duration_seconds` (model processing latency)
    *   `input_tokens` / `output_tokens` (for usage and billing audits)
    *   `tool_calls` (duration, tool name, success/failure status)
    *   `safety_escalation_count` and `report_validation_failures` (quality indicators)
