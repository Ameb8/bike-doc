# Bike Doc Backend Scaffold Spec

Status: Draft v0.1
Last updated: 2026-06-22

Backend root: `apps/api`

This document defines the intended scaffold and organization for the Bike Doc
backend. It is about structure, ownership boundaries, and early conventions.
It does not define agent prompts, database schemas, or route behavior in full;
those details should be covered by follow-up specs and implementation plans.

The backend is a custom FastAPI service that imports Google ADK as a library.
It must not expose ADK sessions, prompts, tools, or model internals directly to
the Android app. The public contract remains the product-level API in
`docs/specs/openapi.yaml`. Cross-cutting public error behavior is defined in
`docs/specs/apps/api-errors.md`. Backend testing conventions are defined in
`docs/specs/apps/api-testing.md`.

## 1. Goals

- Keep all backend code inside the monorepo subdirectory `apps/api`.
- Provide one FastAPI application that owns authentication, product API
  routes, persistence, media uploads, SSE event streaming, safety enforcement,
  and ADK orchestration.
- Keep ADK-specific code isolated behind an internal orchestration boundary.
- Make the initial scaffold useful before feature implementation begins.
- Preserve clear seams for later providers: GCS artifacts, price lookup, and
  repair-reference lookup.
- Keep normal backend tests separate from agent behavior evaluations.

## 2. Non-Goals

- Do not initialize or scaffold Google ADK code yet.
- Do not run `adk api_server` as the application server.
- Do not implement product functionality in the scaffold.
- Do not define final prompts, phase report schemas, or database migrations in
  this document.
- Do not introduce multiple deployable backend services for V1.

## 3. Repository Placement

The backend root should be:

```text
apps/api/
```

The spec path mirrors that root:

```text
docs/specs/apps/api.md
```

The broader monorepo shape should remain:

```text
apps/
  android/
  api/
packages/
  shared-schemas/
infra/
docs/
  specs/
    bike-doc.md
    openapi.yaml
    apps/
      api.md
evals/
  bike-doc/
```

## 4. Backend Root Layout

The backend should use a Python `src/` layout:

```text
apps/api/
  AGENTS.md
  README.md
  pyproject.toml
  alembic.ini

  src/
    bike_doc_api/
      __init__.py
      main.py

      core/
        __init__.py
        config.py
        errors.py
        logging.py
        security.py

      api/
        __init__.py
        router.py
        deps.py
        v1/
          __init__.py
          me.py
          bikes.py
          artifacts.py
          repair_sessions.py
          turns.py
          events.py
          decisions.py
          reports.py

      schemas/
        __init__.py
        common.py
        user.py
        bike.py
        artifact.py
        repair_session.py
        turn.py
        event.py
        decision.py
        report.py

      db/
        __init__.py
        base.py
        session.py
        migrations/

      models/
        __init__.py
        user.py
        bike.py
        artifact.py
        repair_session.py
        repair_history.py
        phase_report.py
        event.py
        tool_catalog.py
        price_lookup.py
        repair_reference.py

      repositories/
        __init__.py
        bikes.py
        artifacts.py
        repair_sessions.py
        events.py
        reports.py
        repair_history.py
        tool_catalog.py
        users.py

      services/
        __init__.py
        auth.py
        artifacts.py
        repair_sessions.py
        turns.py
        events.py
        decisions.py
        safety.py
        reports.py

      adk/
        __init__.py
        runner.py
        sessions.py
        artifacts.py
        orchestration.py
        agents/
          __init__.py
          diagnostic.py
          planning.py
          execution.py
        tools/
          __init__.py
          bike_profile.py
          repair_history.py
          tool_catalog.py
          reports.py
          price_lookup.py
          repair_reference.py
        prompts/
          diagnostic.md
          planning.md
          execution.md
        report_schemas/
          __init__.py
          diagnostic.py
          plan.py
          execution.py

      providers/
        __init__.py
        storage/
          __init__.py
          base.py
          gcs.py
        price/
          __init__.py
          base.py
          stub.py
        repair_reference/
          __init__.py
          base.py
          stub.py

  tests/
    unit/
    api/
    contract/
```

Placeholder files are acceptable in the scaffold when they document intended
ownership. They should avoid fake behavior that looks production-ready.

## 5. Layer Responsibilities

### 5.1 `main.py`

`main.py` creates the FastAPI application, installs global middleware,
registers routers, and configures application startup/shutdown hooks. It should
stay thin. Business logic belongs in services.

Expected responsibilities:

- Create the `FastAPI` app.
- Register `api.router`.
- Register exception handlers from `core.errors`.
- Configure CORS when needed by local development.
- Open and close shared resources during lifespan.

### 5.2 `core/`

`core/` contains cross-cutting infrastructure used by the rest of the app.

- `config.py`: typed settings loaded from environment variables.
- `errors.py`: domain errors and FastAPI exception mappers.
- `logging.py`: logging setup and request correlation conventions.
- `security.py`: JWT bearer-token validation helpers and auth primitives.

This package should not import route modules, repositories, ADK agents, or
provider implementations directly.

### 5.3 `api/`

`api/` owns HTTP routing and request/response adaptation.

- Route files under `api/v1/` should map to the OpenAPI groups in
  `docs/specs/openapi.yaml`.
- Route handlers should validate inputs, resolve dependencies, call services,
  and return response schemas.
- Route handlers should not call repositories, provider SDKs, or ADK runners
  directly.

The intended route grouping is:

| Module | OpenAPI Surface |
|---|---|
| `me.py` | `GET /v1/me` |
| `bikes.py` | bike profiles and repair history reads |
| `artifacts.py` | media upload and artifact metadata |
| `repair_sessions.py` | session create, lookup, list, completion |
| `turns.py` | `POST /v1/repair-sessions/{sessionId}/turns` |
| `events.py` | SSE stream endpoint |
| `decisions.py` | DIY/shop/not-now decision route |
| `reports.py` | phase report reads |

### 5.4 `schemas/`

`schemas/` contains Pydantic request and response models for the product API.

The schemas should track `docs/specs/openapi.yaml`. During early development,
manual Pydantic models are acceptable. As the API stabilizes, the project
should choose one explicit contract workflow:

- Generate OpenAPI from FastAPI and compare it against
  `docs/specs/openapi.yaml`, or
- Treat `docs/specs/openapi.yaml` as canonical and generate server/client
  schemas from it.

The Android client should never depend on internal ADK models.

### 5.5 `db/`

`db/` owns database setup.

- `session.py` creates database engine/session factories.
- `base.py` exposes ORM metadata for migrations.
- `migrations/` holds Alembic revision files.

The same Postgres instance should store application data and ADK session data,
but app tables and ADK tables should remain conceptually separate. The app
should not rely on ADK internal session rows as the source of truth for product
state.

### 5.6 `models/`

`models/` contains ORM models for application-owned tables.

Expected model areas:

- users
- bike profiles
- artifact references
- repair sessions
- repair-session events
- phase reports
- repair history
- preset tool catalog entries
- price lookup cache entries
- repair reference lookup results

These models are persistence representations, not API contracts and not ADK
tool schemas.

### 5.7 `repositories/`

`repositories/` contains persistence operations. Repositories should be small,
transaction-aware wrappers over database queries.

Repositories may know about ORM models and database sessions. They should not
know about FastAPI request objects, ADK runners, prompts, provider SDKs, or SSE
wire formatting.

### 5.8 `services/`

`services/` owns product behavior and workflow rules.

Important service responsibilities:

- `auth.py`: current-user resolution from validated bearer tokens.
- `artifacts.py`: artifact validation, metadata persistence, storage provider
  calls.
- `repair_sessions.py`: session lifecycle and phase state.
- `turns.py`: idempotent user-turn acceptance and agent-processing handoff.
- `events.py`: event persistence, cursors, replay, and SSE stream coordination.
- `decisions.py`: DIY/shop/not-now decision handling.
- `safety.py`: server-enforced safety invariants.
- `reports.py`: phase report persistence and lookup.

Safety enforcement belongs here, not only in prompts. For example, a blocking
safety flag must prevent `decision: diy` regardless of model output.

### 5.9 `adk/`

`adk/` is the internal integration package for Google ADK. This package may be
empty or lightly stubbed in the initial scaffold, but its intended organization
should be documented from the beginning.

Responsibilities:

- Construct ADK agents for each product phase.
- Wrap ADK `Runner` usage.
- Manage ADK session creation for each phase.
- Seed each phase with durable product state and prior structured reports.
- Convert ADK stream output into product events.
- Provide ADK tools that call backend services or provider interfaces.

The main product flow should not use one long-lived ADK session across all
phases. Each phase gets its own ADK session. Phase handoff happens through
structured reports stored by the backend.

#### `adk/agents/`

This directory should contain phase-specific agent factories:

- `diagnostic.py`
- `planning.py`
- `execution.py`

Each phase may have different instructions, tools, model settings, and report
schema requirements. Shared setup should be factored only when it removes real
duplication.

#### `adk/tools/`

ADK tools should be thin wrappers over backend services and provider
interfaces. They should not contain SQL queries or direct SDK calls.

Initial tool modules should correspond to the high-level design:

- `bike_profile.py`
- `repair_history.py`
- `tool_catalog.py`
- `reports.py`
- `price_lookup.py`
- `repair_reference.py`

#### `adk/prompts/`

Prompt files should be versioned as text artifacts. They should not be embedded
as large string literals in Python modules unless there is a strong reason.

#### `adk/report_schemas/`

Report schemas define the structured handoff between phases:

- diagnostic report
- plan report
- execution report

These should be versioned early. The persisted report envelope should store a
schema version so report evolution can be handled explicitly.

### 5.10 `providers/`

`providers/` isolates external service choices from product logic and ADK
tools.

Initial provider areas:

- `storage`: GCS-backed artifact storage, behind a storage interface.
- `price`: stubbed or search-backed price lookup, behind a price interface.
- `repair_reference`: stubbed repair-reference lookup, later replaceable by
  pgvector or another retrieval system.

Provider interfaces should be boring and narrow. Do not expose vendor SDK
objects outside provider implementations.

## 6. Phase and Session Model

The backend owns product-level repair sessions. ADK sessions are internal
implementation details.

```text
RepairSession
  diagnostic phase -> ADK session A -> DiagnosticReport
  planning phase   -> ADK session B -> PlanReport
  execution phase  -> ADK session C -> ExecutionReport
```

Important rules:

- A repair session spans the entire user workflow.
- Multiple user turns can occur inside one phase.
- All turns in one phase share that phase's ADK session.
- When a phase completes, the backend stores a structured report.
- The next phase starts a fresh ADK session seeded only with allowed durable
  state and prior structured reports.
- Full transcripts and media references are archived and queryable, but not
  blindly replayed into later phases.

## 7. Event and SSE Model

The backend should persist repair-session events before or while streaming
them. The SSE endpoint must support mobile reconnects.

Core concepts:

- `POST /turns` accepts a user turn and returns the cursor the client should
  use to stream events.
- `GET /events?after=...` sends persisted events newer than the cursor, then
  waits for live events.
- Event IDs are monotonically increasing within a repair session.
- The wire format follows the OpenAPI contract: `id`, `event`, and JSON
  `data`.

The event service should own cursor semantics. ADK code should emit internal
events or callbacks that are converted into product-level events.

## 8. Authentication Boundary

The API contract assumes bearer tokens from an external identity provider.
The backend validates tokens and maps them to application users.

The scaffold should include a security boundary but not hard-code a final auth
provider. A later implementation can choose Firebase Auth or another provider.

Route handlers should depend on a current-user dependency. Services should
receive the resolved user identity rather than parsing bearer tokens.

## 9. Artifact Boundary

V1 uses server-proxied artifact uploads.

The artifact service should:

- Enforce purpose-specific validation from `docs/specs/openapi.yaml`.
- Store metadata in Postgres.
- Store binary data through the storage provider.
- Return product-level artifact references.
- Avoid leaking raw GCS paths or ADK artifact internals unless intentionally
  included in the API contract.

ADK `GcsArtifactService` can be used later inside the ADK integration layer,
but the product API should continue to expose Bike Doc artifact references.

## 10. Safety Enforcement

Safety is a server invariant, not just an agent instruction.

The safety service should evaluate active safety flags before allowing state
transitions or decisions. At minimum:

- `blocking` flags reject `decision: diy`.
- `warning` flags allow `decision: diy` only when explicitly acknowledged.
- `caution` and `info` are advisory.

These checks should be unit tested independently of ADK behavior.

## 11. Testing Layout

Backend tests live under `apps/api/tests`.
Detailed backend testing conventions live in
`docs/specs/apps/api-testing.md`.

Recommended split:

```text
tests/
  unit/
    services/
    repositories/
    providers/
  api/
    test_bikes.py
    test_repair_sessions.py
    test_turns.py
    test_events.py
    test_decisions.py
  contract/
    test_openapi_contract.py
```

Backend tests should cover:

- request/response validation
- API error mapping
- repository behavior
- idempotency rules
- safety invariants
- event cursor and replay behavior
- provider interface behavior

Backend pytest tests should not assert exact LLM wording or agent answer
quality. Agent behavior belongs in `evals/bike-doc`.

## 12. Evaluation Layout

Agent evaluations should live outside the backend app package:

```text
evals/
  bike-doc/
    README.md
    cases/
    graders/
```

Core eval scenarios should align with `docs/specs/bike-doc.md`:

- asks for missing diagnostic evidence
- escalates safety-critical repairs
- avoids invented torque specs
- creates complete parts/tool plans
- uses catalog-grounded tools when possible
- stops execution when verification photos reveal unexpected damage
- produces clean structured phase reports

## 13. Local Development Conventions

The backend should be runnable from `apps/api`.

Expected commands can be finalized later, but the scaffold should anticipate:

```bash
uv sync
uv run fastapi dev src/bike_doc_api/main.py
uv run pytest
uv run alembic upgrade head
```

Environment variables should be documented in the repository root
`.env.example`, next to `compose.yaml`. Local development should use a
single repository root `.env` copied from that template. Docker Compose may read
that file for interpolation, but it should still pass API configuration
explicitly under the API service's `environment` section.

The baseline API settings are defined in `docs/specs/apps/config-setup.md`.
Feature-specific settings, such as database, auth, model, storage, or provider
configuration, should be added only when the corresponding feature is
implemented.

## 14. Dependency Conventions

The backend should keep runtime dependencies focused:

- FastAPI and ASGI server
- Pydantic settings
- SQLAlchemy or SQLModel
- Alembic
- async Postgres driver
- Google ADK when ADK integration begins
- Google Cloud Storage client when GCS integration begins
- pytest and HTTP test client tooling for tests

Avoid adding provider-specific SDKs outside `providers/` or `adk/` integration
needs.

## 15. Import Direction Rules

Keep import direction predictable:

```text
api -> services -> repositories -> models/db
api -> schemas
services -> providers
services -> adk orchestration
adk tools -> services/providers
```

Avoid:

- repositories importing services
- models importing routes
- provider implementations importing FastAPI request objects
- route handlers importing ADK agents directly
- ADK tools issuing SQL directly

## 16. Initial Scaffold Acceptance Criteria

The initial backend scaffold is complete when:

- `apps/api` exists as the backend root.
- `apps/api/AGENTS.md` explains backend-specific conventions.
- The package root is `src/bike_doc_api`.
- The folder structure reflects the layers in this spec.
- Placeholder modules are minimal and clearly non-functional.
- No ADK project is initialized yet.
- No product functionality is pretending to be implemented.
- The design remains aligned with `docs/specs/bike-doc.md` and
  `docs/specs/openapi.yaml`.

## 17. Follow-Up Specs

Separate specs should define:

- database schema and migration plan
- phase report schema versions
- prompt and tool contracts
- auth provider choice
- artifact storage details
- SSE persistence and replay behavior
- deployment and local development setup
- generated schema/client workflow
