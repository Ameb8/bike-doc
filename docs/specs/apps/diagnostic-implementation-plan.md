# Bike Doc Diagnostic Vertical Slice Roadmap

Status: Draft v0.1
Last updated: 2026-06-21

This roadmap defines the steps to implement the first backend vertical slice:
repair session creation through diagnostic phase completion. The slice is done
when a user can create a repair session, submit diagnostic turns with text and
photos, receive persisted SSE events, and retrieve a schema-valid diagnostic
phase report.

This document is intentionally implementation-oriented. It names the specs that
need to be written, what each spec must contain, what code should be built at
each stage, and when OpenAPI code generation should be used.

## References

- Product design: `docs/specs/bike-doc.md`
- Backend scaffold: `docs/specs/apps/api.md`
- Public API contract: `docs/specs/openapi.yaml`
- Backend root: `apps/api`
- Existing codegen setup: `apps/api/pyproject.toml`

## Scope

In scope:

- Authenticated current-user dependency, with a local development strategy.
- Bike profile read access needed to start a repair session.
- Repair session creation and lookup.
- Diagnostic user turns.
- Artifact upload for diagnostic photos.
- Persisted repair-session event log.
- SSE replay and live streaming behavior for diagnostic events.
- Diagnostic report persistence and read endpoints.
- Safety flags produced during diagnosis and persisted for later decision
  enforcement.
- Diagnostic ADK tool contracts and eventual diagnostic agent wiring.

Out of scope:

- Planning phase implementation.
- Execution phase implementation.
- DIY, shop, or not-now decision endpoint behavior, except for storing safety
  data that endpoint will later consume.
- Android UI implementation.
- Production Firebase/Auth provider hardening beyond the selected boundary.
- Production GCS behavior beyond the provider interface and a basic stub path,
  unless needed to test uploads.

## Guiding Decisions

Use a vertical slice rather than implementing layers across the whole API. The
goal is to prove the hardest backend mechanics early: persistence, idempotency,
events, safety flags, report storage, and the ADK boundary.

Keep these model families separate:

- Public API schemas from `docs/specs/openapi.yaml`.
- SQLAlchemy persistence models.
- ADK tool input/output schemas and internal report schemas.

Generated code should not be moved wholesale into hand-written route modules.
Generate into an isolated package or temporary directory, inspect it, and then
decide whether generated models are clean enough to import. Route handlers
should remain hand-written and thin.

## Required Follow-Up Specs

Write or update these specs before substantial implementation. They can be
small, but they should be explicit enough to prevent important behavior from
being decided accidentally in code.

### 1. Diagnostic API Delta Spec

Suggested path: `docs/specs/apps/api-diagnostic.md`

Include:

- Which existing OpenAPI paths are part of the diagnostic slice.
- Any changes needed to `docs/specs/openapi.yaml`.
- Request and response examples for:
  - `POST /v1/repair-sessions`
  - `GET /v1/repair-sessions/{sessionId}`
  - `POST /v1/repair-sessions/{sessionId}/turns`
  - `GET /v1/repair-sessions/{sessionId}/events`
  - `POST /v1/artifacts`
  - `GET /v1/repair-sessions/{sessionId}/reports`
  - `GET /v1/repair-sessions/{sessionId}/reports/{reportId}`
- Error cases and exact status codes for the diagnostic slice.
- Whether any endpoint can be implemented as a no-agent stub in Stage 2.

#### Definition of Done

- `openapi.yaml` is either unchanged and confirmed sufficient, or patched to
  match the slice.
- Each endpoint has at least one success example and one expected error case.

### 2. Diagnostic DB Schema Spec

Suggested path: `docs/specs/apps/api-db-diagnostic.md`

Include:

- Table names, columns, nullable rules, defaults, and timestamp behavior.
- Primary key format. Prefer one consistent strategy, for example prefixed text
  IDs such as `rs_...`, `turn_...`, `evt_...`.
- Foreign keys and delete behavior.
- Unique constraints for idempotency:
  - one user plus `client_session_id` for repair session creation, if supplied
  - one repair session plus `client_turn_id` for turns
  - one user plus `client_artifact_id`, if supplied
- Indexes for:
  - user-owned bike lookup
  - session lookup by user and status
  - event replay by session and sequence
  - report listing by session and creation time
  - artifact lookup by session or bike
- Enum storage strategy. Prefer stable string values matching OpenAPI enums.
- JSONB usage for:
  - event `data`
  - phase report `payload`
  - safety flags
  - current input request
  - execution progress, if present on the shared session table
- Cursor implementation for pagination and SSE replay.
- Transaction boundaries for creating turns and events.
- How app-owned tables remain separate from ADK session tables.

Minimum diagnostic tables:

- `users`
- `bike_profiles`
- `repair_sessions`
- `repair_turns`
- `repair_session_events`
- `artifact_refs`
- `phase_reports`

#### Definition of Done

- The spec is concrete enough to write one Alembic migration without guessing
  column types, constraints, or indexes.

### 3. Diagnostic Report Schema Spec

Suggested path: `docs/specs/apps/diagnostic-report-v1.md`

Include:

- Confirm `DiagnosticReportV1` fields against `openapi.yaml`.
- Required and optional fields.
- Safety flag semantics.
- Allowed confidence values.
- What `diagnostic_session_id` means. It should be an internal archive/session
  reference, not a public ADK session contract.
- Whether public `schemas/report.py` and internal
  `adk/report_schemas/diagnostic.py` share a class or use separate models with
  an explicit mapper.
- Validation rules for generated report payloads.
- Example reports:
  - straightforward low-risk diagnosis
  - ambiguous diagnosis requiring follow-up
  - blocking safety diagnosis

Recommended decision:

- Use separate API and ADK/internal models with explicit mapping. This protects
  the public API from ADK implementation details and keeps future report
  evolution manageable.

#### Definition of Done

- A diagnostic report produced by the agent can be validated before it is
  persisted and before it is exposed through the API.

### 4. Diagnostic Event And SSE Spec

Suggested path: `docs/specs/apps/api-events-diagnostic.md`

Include:

- Event sequence semantics. Event sequence should be monotonically increasing
  within a repair session.
- Difference between event `id` and event `sequence`, if both are retained.
- How `after` works:
  - omitted means start after the current latest event
  - `0` means replay retained events
  - any known ID or sequence means replay strictly newer events
- How `Last-Event-ID` interacts with `after`.
- Stream timeout behavior.
- Heartbeat behavior.
- Which OpenAPI event types are used in the diagnostic slice:
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
- Internal-to-public event mapping. Avoid inventing a second public taxonomy.
- Persistence timing. Persist events before or while streaming, never only in
  memory.
- Replay retention policy for V1.

#### Definition of Done

- API tests can assert event replay behavior without needing an ADK model call.

### 5. Diagnostic Tool Contracts Spec

Suggested path: `docs/specs/apps/adk-diagnostic-tools.md`

Include:

- Tool names and ownership:
  - `get_bike_profile`
  - `lookup_repair_history`
  - `save_diagnostic_report`
- Input schema for each tool.
- Output schema for each tool.
- Error behavior for missing resources, unauthorized access, invalid report
  payloads, and stale sessions.
- Which service each tool calls.
- Explicit rule that ADK tools do not issue SQL directly.
- Whether `lookup_repair_reference` is excluded from diagnosis and reserved
  for execution.

Recommended decision:

- Keep `lookup_repair_reference` out of the diagnostic slice unless a specific
  diagnostic use case requires authoritative service/manual data. The current
  design points that tool at execution.

#### Definition of Done

- ADK tool modules can be implemented as thin wrappers without inventing
  behavior.

### 6. Diagnostic Safety Spec

Suggested path: `docs/specs/apps/safety-diagnostic.md`

Include:

- Safety flag codes used in diagnostic V1.
- Severity rules for `info`, `caution`, `warning`, and `blocking`.
- When `blocks_repair_instructions` should be true.
- How diagnostic safety flags affect `repair_sessions.safety_state`.
- What must be persisted for the later decision endpoint.
- Validation behavior for malformed or contradictory model-produced safety
  flags.

#### Definition of Done

- `services/safety.py` can be unit tested without ADK.

### 7. Auth And Local Development Spec

Suggested path: `docs/specs/apps/api-auth-dev.md`

Include:

- Production auth boundary: bearer JWT from external provider.
- Local development strategy:
  - fixed dev token
  - local unsigned token fixture
  - or dependency override in tests
- How a validated identity maps to `users`.
- Whether users are auto-created on first authenticated request.
- Required user fields and defaults, especially `skill_level`.
- Error behavior for invalid token, missing token, and known token with no
  mapped user.

#### Definition of Done

- API tests can authenticate deterministically.

### 8. Artifact Storage Spec

Suggested path: `docs/specs/apps/api-artifacts-diagnostic.md`

Include:

- Purpose-specific validation rules from OpenAPI.
- Diagnostic-photo allowed MIME types.
- Max upload size.
- Filename handling.
- Whether dimensions are extracted in V1.
- Storage provider interface.
- Local stub storage behavior.
- GCS object naming strategy for future production use.
- Relationship between artifacts, repair sessions, bikes, and users.

#### Definition of Done

- Upload endpoint behavior can be implemented and tested without real GCS.

### 9. Code Generation Workflow Spec

Suggested path: `docs/specs/apps/api-codegen.md`

Include:

- Whether `docs/specs/openapi.yaml` is canonical.
- Where generated code is written.
- Whether generated files are committed.
- Whether generated routers are used at all.
- Whether generated Pydantic models are imported by hand-written routes or only
  used for contract checks.
- Regeneration command from `apps/api/pyproject.toml`.
- Review checklist for generated output.

Recommended decision for this slice:

- Treat `openapi.yaml` as canonical.
- Generate into `src/bike_doc_api/generated` or a temporary directory.
- Do not move generated endpoint stubs into `api/v1`.
- Prefer hand-written route modules that import generated schemas only if the
  generated models are readable and stable.
- If generated models are awkward around `oneOf`, discriminators, SSE, or
  multipart uploads, keep generated output as a contract check and write manual
  Pydantic models for the slice.

#### Definition of Done

- The team can regenerate without overwriting hand-written behavior.

## Implementation Stages

### Stage 0: Baseline Verification

Goal: know the current scaffold state before changing behavior.

#### Tasks

- Confirm `apps/api` starts as an empty FastAPI shell.
- Run existing tests, even if only placeholders exist.
- Run Ruff check and format on current code.
- Confirm Postgres starts through Docker Compose.
- Confirm Alembic environment imports the app metadata.
- Record any setup failures before implementing domain code.

Suggested commands:

```bash
cd apps/api
uv sync --group dev --group codegen
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

#### Definition of Done

- You know whether failures are pre-existing scaffold issues or caused by later
  implementation.

### Stage 1: Write The Missing Specs

Goal: remove ambiguity before code starts encoding accidental decisions.

#### Tasks

- Write the nine specs listed above.
- Patch `docs/specs/openapi.yaml` if the diagnostic API delta spec finds drift.
- Keep changes narrow. Do not design planning or execution in detail here.
- Review the specs against these invariants:
  - public API does not expose ADK internals
  - app tables are the product source of truth
  - safety is enforced in backend services
  - SSE events are persisted and replayable
  - idempotency is backed by database constraints

#### Definition of Done

- OpenAPI, DB shape, report schema, event semantics, auth, artifact behavior,
  and codegen policy are explicit enough to implement.

### Stage 2: Codegen Spike, Do Not Integrate Yet

Goal: validate that the OpenAPI file can generate usable code before hand
writing or importing schemas.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-codegen.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Run codegen into an isolated output.
- Inspect generated models for:
  - Pydantic v2 compatibility
  - enum naming quality
  - nullable field handling
  - `oneOf` and discriminator handling for reports and events
  - multipart upload handling
  - generated router shape
- Decide whether generated models are acceptable for import.
- Do not move generated routers into `api/v1`.
- Do not hand edit generated files.

Suggested command from `apps/api`:

```bash
uv run --group codegen fastapi-codegen \
  --input ../../docs/specs/openapi.yaml \
  --output src/bike_doc_api/generated \
  --generate-routers \
  --output-model-type pydantic_v2.BaseModel \
  --python-version 3.12 \
  --use-annotated \
  --strict-nullable \
  --disable-timestamp
```

Decision point:

- If generated models are clean, keep them isolated in
  `bike_doc_api.generated` and import them from hand-written routes/services
  where useful.
- If generated models are not clean, delete or ignore generated output and
  write manual Pydantic models for only the diagnostic slice, with contract
  tests comparing app OpenAPI to `docs/specs/openapi.yaml`.

#### Definition of Done

- Codegen has been evaluated, and the project has a deliberate choice instead
  of generated code owning the architecture by accident.

### Stage 3: Persistence Backbone

Goal: create the durable data model before endpoint behavior depends on it.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement SQLAlchemy models for the minimum diagnostic tables:
  - `models/user.py`
  - `models/bike.py`
  - `models/repair_session.py`
  - `models/artifact.py`
  - `models/event.py`
  - `models/phase_report.py`
  - `models/repair_history.py`, if diagnostic history lookup is included
- Import all models into `db/base.py` or Alembic env wiring so migrations see
  metadata.
- Write the first Alembic migration.
- Add repository skeletons for each table.
- Add unit tests for repository create/get/list paths.
- Use JSONB where the DB schema spec calls for JSON payloads.
- Add database uniqueness constraints for idempotency.

Code generation:

- Do not generate endpoint code in this stage.
- Do not generate SQLAlchemy models from OpenAPI. The OpenAPI schemas are API
  contracts, not persistence contracts.

#### Definition of Done

- `uv run alembic upgrade head` creates all diagnostic tables.
- Repository tests can insert and read users, bikes, sessions, events,
  artifacts, and reports.

### Stage 4: API Schemas

Goal: establish request and response models for the diagnostic slice.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-codegen.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/openapi.yaml`

#### Tasks

- If generated models were accepted, import them from
  `bike_doc_api.generated`.
- If not, implement manual Pydantic v2 schemas in:
  - `schemas/common.py`
  - `schemas/user.py`
  - `schemas/bike.py`
  - `schemas/artifact.py`
  - `schemas/repair_session.py`
  - `schemas/turn.py`
  - `schemas/event.py`
  - `schemas/report.py`
- Add mapper functions between ORM models and API schemas.
- Add schema validation tests for:
  - diagnostic report envelope
  - safety flags
  - turn create request
  - artifact reference
  - repair session response

Code generation:

- This is the first stage where generated Pydantic models may be used in app
  code.
- Do not copy generated files into `schemas/`. Either import from
  `generated/` or write manual owned models.

#### Definition of Done

- The diagnostic slice has stable API models and mapping functions.

### Stage 5: Contract Tests First

Goal: turn the OpenAPI contract into executable expectations before route
behavior exists.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-auth-dev.md`
- `docs/specs/apps/api-artifacts-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/apps/api-events-diagnostic.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Add API tests under `apps/api/tests/api`.
- Add contract tests under `apps/api/tests/contract`.
- Cover:
  - authenticated current user setup
  - `POST /v1/repair-sessions`
  - `GET /v1/repair-sessions/{sessionId}`
  - `POST /v1/repair-sessions/{sessionId}/turns`
  - repeated `client_turn_id` idempotency
  - `GET /v1/repair-sessions/{sessionId}/events?after=0`
  - artifact upload validation for `diagnostic_photo`
  - report listing and lookup
  - expected 404, 409, and 422 cases
- Add an app OpenAPI snapshot or diff test if using manual schemas.

Code generation:

- If generated routers exist, use them only as a reference for route signatures.
- Keep production route modules hand-written.

#### Definition of Done

- Tests are red for missing behavior but reflect the intended API precisely.

### Stage 6: Auth And User Resolution

Goal: make route tests authenticate without committing to a full production
identity provider.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-auth-dev.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement `core/security.py` primitives.
- Implement `services/auth.py`.
- Implement dependency in `api/deps.py`.
- Add deterministic test auth override.
- Add optional dev-token behavior if the auth spec allows it.
- Decide whether first authenticated request auto-creates a `users` row.

Code generation:

- None.

#### Definition of Done

- API tests can resolve a current user and reject missing or invalid auth.

### Stage 7: Repair Session Service And Routes

Goal: create and read diagnostic repair sessions without ADK.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement `repositories/users.py`, `repositories/bikes.py`, and
  `repositories/repair_sessions.py` paths needed for session creation.
- Implement `services/repair_sessions.py`:
  - create session
  - enforce bike ownership
  - initialize `phase = diagnostic`
  - initialize `status`
  - initialize `safety_state`
  - handle `client_session_id` idempotency if supported
  - retrieve session state
  - list sessions if included in this slice
- Implement routes in `api/v1/repair_sessions.py`.
- Add route tests.

Code generation:

- Do not generate route behavior.

#### Definition of Done

- Repair session create/get tests pass.

### Stage 8: Event Log Service

Goal: persist and replay repair-session events before ADK is wired.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/apps/api-events-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement `repositories/events.py`.
- Implement `services/events.py`:
  - append event transactionally
  - allocate monotonic sequence per session
  - return latest event cursor
  - list events after cursor
  - format SSE frames
  - emit heartbeat
  - timeout stream cleanly
- Implement route in `api/v1/events.py`.
- Add tests for:
  - replay all with `after=0`
  - replay newer than a known cursor
  - omitted `after` starts after current latest event
  - unknown session returns 404

Code generation:

- Generated event schemas may be useful here if they are clean.
- SSE streaming implementation should be hand-written.

#### Definition of Done

- Event replay works without ADK or live model output.

### Stage 9: Artifact Upload Service

Goal: support diagnostic photos through the product artifact boundary.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-artifacts-diagnostic.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement provider interface in `providers/storage/base.py`.
- Implement local/stub storage for tests and local development.
- Leave GCS implementation minimal or unimplemented until production storage
  is needed.
- Implement `repositories/artifacts.py`.
- Implement `services/artifacts.py`:
  - validate `purpose`
  - require `repair_session_id` for `diagnostic_photo`
  - reject `bike_id` for `diagnostic_photo`
  - validate ownership
  - enforce size and MIME limits
  - store metadata
  - call storage provider
  - handle `client_artifact_id` idempotency if supplied
- Implement routes in `api/v1/artifacts.py`.
- Add upload tests with in-memory files.

Code generation:

- Do not rely on generated multipart route handlers. Multipart and storage
  behavior should remain hand-written.

#### Definition of Done

- Diagnostic photo upload returns an `ArtifactRef` and persists metadata.

### Stage 10: Turn Acceptance Service

Goal: accept user diagnostic turns and create events without ADK.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/apps/api-events-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement `repair_turns` repository support.
- Implement `services/turns.py`:
  - validate session ownership
  - validate session phase/status accepts turns
  - validate `client_turn_id`
  - validate artifact IDs belong to the session/user
  - persist the turn
  - append `turn.started`
  - append a temporary non-LLM response event or `input.requested` event only
    if the diagnostic API delta spec allows a no-agent stub
  - append `turn.completed` when stub processing ends
  - return `TurnAccepted` with `start_event_id`
- Implement route in `api/v1/turns.py`.
- Add tests for idempotent retry.

Code generation:

- Generated request and response schemas may be used.
- Route behavior remains hand-written.

#### Definition of Done

- Text-only diagnostic turns can be accepted and replayed through the event
  endpoint.

### Stage 11: Diagnostic Report Persistence

Goal: persist and expose diagnostic reports before ADK produces them.

#### Relevant Specs for Implementation

- `docs/specs/apps/api.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/apps/api-events-diagnostic.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/apps/safety-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement `repositories/reports.py`.
- Implement `services/reports.py`:
  - validate report envelope
  - persist report payload
  - persist safety flags
  - update latest diagnostic report on session
  - append `phase.report.created`
  - transition session to the next appropriate status
- Implement read routes in `api/v1/reports.py`.
- Add test helper to create a valid diagnostic report without ADK.
- Add report list/get tests.

Code generation:

- Generated `DiagnosticReportV1` and `PhaseReportEnvelope` models can be used
  if accepted in Stage 2.
- If generated union/discriminator behavior is poor, use manual report models
  and preserve contract tests.

#### Definition of Done

- A schema-valid diagnostic report can be persisted and retrieved over the API.

### Stage 12: Safety Service

Goal: enforce server-side safety semantics independent of prompts.

#### Relevant Specs for Implementation

- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/apps/safety-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement `services/safety.py`.
- Validate model-produced safety flags before persistence.
- Map active safety flags to session `safety_state`.
- Persist enough information for the later decision endpoint to reject
  `decision: diy` when blocking flags are active.
- Add unit tests for:
  - info flags
  - caution flags
  - warning flags
  - blocking flags
  - malformed severity
  - `blocks_repair_instructions` consistency

Code generation:

- None.

#### Definition of Done

- Safety behavior is tested without ADK.

### Stage 13: ADK Tool Implementations

Goal: expose backend capabilities to the diagnostic agent through thin tools.

#### Relevant Specs for Implementation

- `docs/specs/apps/adk-diagnostic-tools.md`
- `docs/specs/apps/api.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/apps/safety-diagnostic.md`
- `docs/specs/bike-doc.md`

#### Tasks

- Implement `adk/tools/bike_profile.py`.
- Implement `adk/tools/repair_history.py`.
- Implement `adk/tools/reports.py` with `save_diagnostic_report`.
- Ensure tools call services or providers, not repositories directly unless the
  tool contract spec explicitly allows it.
- Add unit tests with fake services.
- Ensure tool input/output schemas match `adk-diagnostic-tools.md`.

Code generation:

- Do not use OpenAPI endpoint stubs for ADK tools.
- Internal tool schemas may reuse internal Pydantic report models if clean.

#### Definition of Done

- Diagnostic tools are unit-testable without model calls.

### Stage 14: Diagnostic Agent And Prompt

Goal: create the diagnostic agent behind the internal ADK boundary.

#### Relevant Specs for Implementation

- `docs/specs/apps/adk-diagnostic-tools.md`
- `docs/specs/apps/api.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/apps/safety-diagnostic.md`
- `docs/specs/bike-doc.md`

#### Tasks

- Implement `adk/agents/diagnostic.py`.
- Fill `adk/prompts/diagnostic.md`.
- Configure model settings through `core/config.py`.
- Register diagnostic tools.
- Define the completion condition:
  - agent calls `save_diagnostic_report`
  - backend validates and persists report
  - backend emits report and phase transition events
- Add dry-run or structural checks supported by the ADK tooling available in
  the project.

Prompt must cover:

- Ask for missing diagnostic evidence before concluding.
- Treat photos as first-class evidence.
- Track alternate hypotheses.
- Avoid invented torque specs or manufacturer-specific claims.
- Raise safety flags with correct severity.
- Prefer shop referral when risk is high or confidence is low.
- Produce a valid `diagnostic_report.v1` payload.

Code generation:

- None.

#### Definition of Done

- The diagnostic agent can be invoked in isolation with fake service/tool
  dependencies.

### Stage 15: Orchestration Wiring

Goal: connect turn acceptance to ADK and event streaming.

#### Relevant Specs for Implementation

- `docs/specs/apps/adk-diagnostic-tools.md`
- `docs/specs/apps/api.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/apps/api-events-diagnostic.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/apps/safety-diagnostic.md`
- `docs/specs/bike-doc.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Implement `adk/sessions.py` for phase-scoped ADK sessions.
- Implement `adk/runner.py` wrapper.
- Implement `adk/orchestration.py`.
- On first diagnostic turn, create or resume the diagnostic ADK session for the
  repair session.
- Seed ADK with:
  - current user identity
  - bike profile
  - relevant repair history
  - current diagnostic artifacts
- Convert ADK output into persisted product events.
- Ensure route handlers do not import ADK agents directly.
- Ensure all externally visible events use OpenAPI event types.

Code generation:

- None.

#### Definition of Done

- A real diagnostic turn can produce persisted assistant events and, when
  complete, a persisted diagnostic report.

### Stage 16: Diagnostic Evals

Goal: test model behavior separately from backend unit and API tests.

#### Relevant Specs for Implementation

- `docs/specs/apps/adk-diagnostic-tools.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/apps/safety-diagnostic.md`
- `docs/specs/bike-doc.md`

#### Tasks

- Create `evals/bike-doc` structure if missing.
- Add cases for:
  - asks for missing diagnostic evidence
  - requests a photo for ambiguous symptoms
  - escalates suspected frame or fork damage
  - avoids invented torque specs
  - produces schema-valid diagnostic report
  - does not escalate trivial low-risk issues
- Add graders for:
  - schema validity
  - evidence seeking
  - safety severity
  - hallucinated torque/manufacturer claims
  - report completeness

Code generation:

- None.

#### Definition of Done

- Agent behavior can regress or improve independently of backend API tests.

### Stage 17: End-To-End Diagnostic Test

Goal: prove the full vertical slice without Android.

#### Relevant Specs for Implementation

- `docs/specs/apps/adk-diagnostic-tools.md`
- `docs/specs/apps/api.md`
- `docs/specs/apps/api-auth-dev.md`
- `docs/specs/apps/api-artifacts-diagnostic.md`
- `docs/specs/apps/api-codegen.md`
- `docs/specs/apps/api-db-diagnostic.md`
- `docs/specs/apps/api-diagnostic.md`
- `docs/specs/apps/api-events-diagnostic.md`
- `docs/specs/apps/diagnostic-report-v1.md`
- `docs/specs/apps/safety-diagnostic.md`
- `docs/specs/openapi.yaml`

#### Tasks

- Use API tests or an integration script to run:
  - create or resolve user
  - create bike profile fixture
  - create repair session
  - submit text turn
  - stream/replay events
  - upload diagnostic photo
  - submit photo-linked turn
  - stream/replay events
  - complete diagnostic phase
  - retrieve diagnostic report
- Assert:
  - response schemas match OpenAPI expectations
  - events are persisted and ordered
  - idempotency works
  - report is schema-valid
  - safety flags update session state
  - no ADK internals leak through the public API

Code generation:

- Run codegen again as a contract drift check.
- If generated output is committed, verify there is no unexpected diff.
- If generated output is not committed, verify generation still succeeds.

#### Definition of Done

- The diagnostic slice is demonstrable with only HTTP/SSE and no Android UI.

## Code Generation Timeline

Use code generation at these points:

1. After Stage 1, as a spike to validate `openapi.yaml`.
2. During Stage 4, only if generated Pydantic models are accepted for import.
3. During Stage 17, as a drift check before declaring the slice complete.

Do not use code generation for:

- SQLAlchemy models.
- Alembic migrations.
- Business services.
- Repositories.
- ADK tools.
- SSE streaming mechanics.
- Production route behavior.

Do not move generated endpoint stubs into `api/v1`. Generated routers are useful
as a reference, but this backend needs hand-written route modules that call the
service layer and preserve the import boundaries in `docs/specs/apps/api.md`.

## Suggested Implementation Order Summary

1. Write missing specs.
2. Run isolated codegen spike.
3. Implement DB models and first migration.
4. Implement repositories.
5. Implement or import API schemas.
6. Write failing contract/API tests.
7. Implement auth dependency.
8. Implement repair session create/get.
9. Implement event log and replay.
10. Implement artifact upload.
11. Implement turn acceptance without ADK.
12. Implement report persistence without ADK.
13. Implement safety service.
14. Implement ADK tools.
15. Implement diagnostic agent and prompt.
16. Wire orchestration.
17. Add evals.
18. Run full end-to-end diagnostic test and codegen drift check.

## Final Acceptance Criteria

The diagnostic vertical slice is complete when:

- Specs listed in this roadmap exist and match implementation.
- Database migration creates the diagnostic persistence backbone.
- API tests pass for session creation, turns, events, artifacts, reports, and
  expected errors.
- Unit tests pass for safety and repository behavior.
- SSE replay works after reconnect.
- Diagnostic report payloads validate against `diagnostic_report.v1`.
- Safety flags are persisted and reflected in session state.
- Diagnostic ADK tools are thin wrappers over services.
- Diagnostic agent can produce a persisted report through orchestration.
- Evals cover core diagnostic behavior.
- Codegen succeeds without forcing generated routers into hand-written modules.
- Public API responses expose product-level resources, not ADK internals.
