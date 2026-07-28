# API Architecture

Bike Doc API is an asynchronous FastAPI service for the product's authenticated
bike-repair workflow. It owns the public HTTP and SSE contract, application
users, bike profiles, repair sessions, uploaded diagnostic photos, durable turn
and event history, safety state, and phase reports. PostgreSQL is the durable
source of truth. Google ADK is an internal library used to execute the
diagnostic agent; it is not a second public server and its types are not part of
the API contract.

Read this document before work that crosses backend modules or changes a
workflow boundary. For a local edit, start with the source and its closest
tests instead. The dedicated [ADK architecture](src/bike_doc_api/adk/ARCHITECTURE.md)
covers the agent package's internal graph, prompts, runner, sessions, and tool
catalog in more depth.

## Quick map

### What the service does

The current implementation supports the diagnostic slice: resolve a bearer
identity to an application user; manage bikes and diagnostic repair sessions;
accept diagnostic photos and user turns; run a diagnostic agent in the
background; persist public events for replay over SSE; and store/read
structured diagnostic reports. Planning and execution concepts exist in shared
schemas and the ADK layout, but the active HTTP workflow is diagnostic-first.

### Major modules

| Area | Owns | Main entry points |
| --- | --- | --- |
| `main.py`, `core/` | App construction, settings, logging, security primitives, and the public error envelope | `create_app`, `Settings`, `install_exception_handlers` |
| `api/` | HTTP/SSE adaptation and dependency composition | `api/router.py`, `api/deps.py`, `api/v1/` |
| `schemas/` | Pydantic public request, response, event, and report shapes | Model conversion helpers beside each schema |
| `services/` | Product rules, ownership checks, workflow state, idempotency, and transaction-level coordination | `TurnService`, `DiagnosticVisualContextService`, `EventService`, `ReportService`, `DiagnosticSafetyService` |
| `repositories/`, `models/`, `db/` | Async SQLAlchemy access, durable records (including session-scoped image-observation extraction runs and ordered provider attempts), metadata, sessions, and Alembic migrations | `db/session.py`, `db/migrations/`, repository classes |
| `providers/` | Replaceable storage, price-lookup, and isolated diagnostic-observation extraction integrations | `StorageProvider`, `PriceLookupProvider`, `DiagnosticObservationExtractor` |
| `adk/` | Internal agent construction, ADK session/runner adaptation, tool adapters, and turn orchestration | `orchestration.py`, `background.py` |

### Dependency direction

Keep the normal direction one way:

```text
HTTP/SSE -> api -> services -> repositories -> models/db
                |       -> providers
                |       -> adk orchestration boundary
adk tools -----------------> services/providers
schemas <------------------- api and services (public shapes only)
```

`main.py`, `api/deps.py`, and `adk/background.py` are composition roots: they
choose concrete repositories, providers, ADK sessions, and services. They may
wire layers together; ordinary route handlers and domain services should not.
In particular, routes do not import ADK agents, repositories do not import
services, and ADK tools do not issue SQL. `background.py` currently reuses a
few provider factories from `api.deps`; treat that as wiring, not as permission
for ADK code to depend on FastAPI transport concerns.

### Main entry points

- [`main.py`](src/bike_doc_api/main.py) creates the FastAPI application,
  validates artifact-storage configuration, configures logging, installs error
  handlers, CORS, and the `/v1` router.
- [`api/router.py`](src/bike_doc_api/api/router.py) assembles versioned public
  route modules. `api/v1/` is the place to add a public endpoint group.
- [`api/deps.py`](src/bike_doc_api/api/deps.py) supplies request-scoped database
  sessions and authenticated users, configured providers, and the process-wide
  in-memory ADK session service.
- [`adk/background.py`](src/bike_doc_api/adk/background.py) is the diagnostic
  background composition root invoked after a new turn is accepted.
- [`db/migrations/`](src/bike_doc_api/db/migrations/) owns durable-schema
  evolution; models alone do not change deployed databases.

## Request and workflow paths

### Standard HTTP request

For a typical authenticated resource request, the path is:

```text
v1 route -> FastAPI dependencies -> service -> repository -> PostgreSQL
                                     -> public Pydantic response schema
```

The `get_current_user` dependency validates a bearer token through
`core/security.py`, then `AuthService` maps the normalized external subject to
an app-owned `User` row (creating it safely on first use). Routes build or
request a service, pass the resolved user and validated Pydantic input, and
return public schemas. Services, rather than routes, perform ownership and
state checks. `core/errors.py` maps expected `AppError` subclasses and FastAPI
validation failures into the OpenAPI `ErrorResponse` envelope.

The database dependency yields an `AsyncSession`, commits on success, and
rolls back on errors. Services that need an atomic multi-record transition use
the supplied commit/rollback callbacks and, where required, a locked
repair-session lookup. Do not hand ORM models or `AsyncSession` objects to a
provider or expose them from an API schema.

### Diagnostic turn

`POST /v1/repair-sessions/{sessionId}/turns` deliberately separates fast,
durable acceptance from model execution:

```text
turn route
  -> TurnService accepts, locks, validates, and persists turn.started
  -> FastAPI BackgroundTasks
  -> adk/background builds a fresh service/repository graph
  -> DiagnosticTurnOrchestrator prepares current-turn visual context, seeds durable context, and streams DiagnosticRunner
  -> ADK tools call services; runner events become public events
  -> EventService persists/commits events, then local SSE fan-out
```

`TurnService` validates session ownership and diagnostic state, validates
referenced artifacts, and enforces client-turn idempotency using a canonical
request hash. It creates or resumes one diagnostic phase session, persists the
turn and its `turn.started` event together, changes the repair session to
running, and commits before returning `202 Accepted`. An idempotent replay does
not start a second background execution.

The background task opens a new database session and reconstructs the
orchestration graph, including `DiagnosticVisualContextService` with fresh
turn, repair-session, artifact, storage, settings, and preprocessing
dependencies. Before building the runner request, the orchestrator prepares
only the accepted turn's images. `pixels_only` supplies labeled normalized
pixels, per-artifact statuses, and empty observation projections; `shadow`
also persists one isolated extraction run and attempt history but intentionally
supplies those same empty projections; `enabled` supplies the current run's
validated score-free observations, assessability, and follow-up projection with
the same pixels, plus completed non-redacted enabled projections from earlier
turns in the repair session. Earlier turns never reload artifact bytes. `off` never
reads pixels and supplies uninspected statuses. An image-only turn for which
the agent cannot be invoked persists its safe recoverable error and terminal
awaiting-user event without invoking the runner. `DiagnosticRunner` translates
Google ADK output into app-owned event objects; no raw ADK event, prompt, tool
trace, model setting, or ADK session ID crosses that boundary. Profile
inference is separately scheduled after diagnostic processing and does not
delay it.

The visual-context service seam is verified with deterministic storage and
extractor fakes plus real encoded image fixtures for one through three current
artifacts in every rollout mode. API, runner, event, report, safety, recovery,
and invalidation tests cover the adjacent durable/public seams; live-model
quality remains in the separate evaluation workflow.

State-mutating tools run directly inside ADK's tool loop. The input-request,
safety-flag, and report tools call the corresponding backend services, which
perform the authoritative write once. The resulting normalized runner event is
a notification for orchestration and flow control, not an instruction to write
the same state a second time. Background setup or runner failures become a
safe, retryable public error followed by a terminal turn event where possible;
there is no automatic whole-turn retry.

### Event replay and streaming

The event endpoint first validates session ownership and resolves the `after`
query cursor or `Last-Event-ID`; `EventService` then emits persisted events in
sequence order and waits for new local events. SSE formatting lives in the
event service, not the route. `EventService.append_event` validates the public
event data, atomically allocates the session sequence through the repository,
commits it, and only then publishes it to its local in-process broker.

The durable `repair_session_events` log, not the local broker or ADK state, is
the reconnect mechanism. The broker is intentionally same-process/same-worker
fan-out today, and the ADK session service is also in memory. A restart can
make a persisted ADK session mapping stale; the runner returns a recoverable
error rather than silently replacing that session. Likewise, any event path
that writes a row directly as part of a larger state transaction must preserve
replay correctness and should be assessed for immediate live notification.
These limits matter before adding multiple workers or a durable job/session
backend.

## Module reference

### `api/`

`api/` is the transport boundary. `router.py` joins route modules for auth,
bikes, artifacts, repair sessions, turns, events, decisions, and reports.
Route modules may use `Depends`, request/response types, headers, SSE response
formatting, and service factories. Keep them thin: validate transport input,
resolve dependencies, call a service, and map its result. Add reusable wiring
to `deps.py`; do not parse bearer tokens in individual routes or create ad hoc
database sessions.

### `schemas/`

This package defines the public Pydantic V2 contract independently from
SQLAlchemy and ADK. It contains the client-visible IDs, status/phase enums,
event payload validation, report envelope, and model-to-schema conversion
helpers. Public API changes begin here and in
[`docs/specs/openapi.yaml`](../../docs/specs/openapi.yaml), not in an ORM model.
Schemas may be used by API and services, but they must never require a FastAPI
request or expose provider/ADK internals.

### `services/`

Services own behavior that spans records or integrations and depend on narrow
repository/provider protocols so unit tests can use fakes. `bikes.py` handles
user-owned profiles and protected soft deletion; `repair_sessions.py` owns the
diagnostic session lifecycle and the service views needed by agent tools;
`artifacts.py` validates uploads, manages storage/metadata consistency, and
returns safe references. `turns.py`, `events.py`, `reports.py`, and `safety.py`
are the diagnostic workflow's core state owners.

Use a service for authorization beyond simple route authentication, state
transitions, idempotency, safety enforcement, external-provider degradation,
or a write that changes more than one aggregate. A provider should never be
the only location where a product rule is enforced. `decisions.py` is presently
a placeholder: do not infer a completed decision workflow merely from the
shared decision schema.

### `repositories/`, `models/`, and `db/`

Models represent stored records: users and bikes; repair sessions, phase
sessions, and turns; artifacts; ordered repair-session events; phase reports;
and image-observation extraction runs. An extraction run is the one durable
visual-evidence record for an accepted image-bearing turn; its ordered provider
attempts are execution history rather than additional evidence. `repair_sessions.py` is the central persistence model for the
long-lived product workflow. A repair session is app-owned; a phase-session
row maps a product phase to its opaque internal ADK session ID. Repositories
encapsulate SQLAlchemy queries, including owner-scoped and `FOR UPDATE` reads;
they return ORM models and do not decide public HTTP behavior.

`db/session.py` is the async engine/session boundary. Artifact lifecycle callers
use the internal `DiagnosticEvidenceInvalidationService` hook when an artifact
becomes inaccessible. It redacts every citing observation-extraction run and
makes citing reports ineligible for ordinary evidence reads; it is not a public
deletion endpoint. Alembic migrations are
the authoritative record of table, constraint, and index changes. When adding
or changing persisted behavior, update model, repository, migration, and the
tests/spec that define its observable semantics as appropriate.

### `providers/`

Providers isolate replaceable infrastructure behind protocols. Artifact bytes
go through `StorageProvider` with local and GCS implementations; the public
artifact response carries app-level metadata, never bucket or object paths.
`PriceLookupProvider` has an unavailable implementation and a Gemini-grounded
implementation. `CostEstimateService` owns validation, result alignment, and
the explicit degraded/unavailable result, so a provider outage does not turn
into fabricated pricing. Repair-reference and tool-catalog packages are
reserved integration seams rather than public API dependencies.

### `adk/`

`adk/` owns the Google ADK seam: agent construction, prompts, ADK sessions,
runner normalization, tool adapters, and orchestration. Its public-to-the-rest-
of-app surface is deliberately small: app-owned runner request/events, opaque
phase-session handling, and tools that call services. See the
[ADK architecture](src/bike_doc_api/adk/ARCHITECTURE.md) for internal details.
Do not import an agent from a route or use an ADK object in `schemas/`.

### `core/`

`core/config.py` centralizes typed `BIKE_DOC_API_` settings and runtime
validation for auth, artifact storage, and model/provider credentials.
`security.py` validates dev, local-fixture, or Firebase bearer identities;
production settings permit Firebase only. `errors.py` is the sole public error
mapping point, while `logging.py` configures process logging. Configuration
must enter at application setup or dependency boundaries, never through direct
environment reads in feature modules.

## Important seams and cross-cutting invariants

- **Authentication and ownership:** validate a bearer token once at the API
  boundary, resolve it to an app `User`, and pass that user to services. All
  user-owned resources must be queried or verified owner-scoped; return the
  normal not-found behavior rather than disclosing another user's data.
- **Persistence and state:** repair-session status, phase, current input
  request, active safety flags, latest event sequence, turns, reports, and
  events are product state. Persist a coherent state transition before clients
  rely on it. Use row locking and existing service paths for concurrent turn
  acceptance and idempotency races.
- **Event durability:** public event payloads are validated against schemas and
  assigned a monotonically increasing sequence per repair session. Persist and
  commit before broker delivery; clients must be able to resume from a cursor.
- **Safety:** prompts can request safe behavior but cannot enforce it.
  `SafetyService`/`DiagnosticSafetyService` validate and reconcile flags,
  derive safety state, persist the change, and emit its product event. Reports
  are also safety-validated before persistence and may move a session to
  `blocked_safety`.
- **Agent boundary:** ADK session IDs, prompts, raw events, tool traces, and
  model configuration remain internal. Phase sessions use app-owned IDs
  publicly, and durable reports—not a blindly replayed transcript—are the
  intended bridge between phases.
- **Artifact boundary:** validate size, MIME type, ownership, attachment, and
  client idempotency in `ArtifactService`; store bytes through a provider and
  preserve only the safe artifact reference for API/agent use. An ADK tool gets
  approved metadata, not a storage path or signed URL.
- **Async and errors:** endpoints, persistence, providers, and orchestration
  are asynchronous. Expected failures use `AppError` subclasses and the common
  error envelope; unexpected provider/agent exceptions must be converted to a
  safe public failure at their appropriate boundary.

## Testing map

Keep tests under `apps/api/tests` and test behavior at the lowest useful
boundary. `tests/unit/services/` covers state rules, safety, idempotency,
events, reports, and cost-estimate degradation with repository/provider fakes.
`tests/unit/adk/` covers the runner's normalized event contract, session
handling, orchestration, and individual tool adapters; it must not require a
live model. Repository/model tests cover persistence-specific behavior, and
provider tests cover their protocol implementations.

`tests/api/` exercises externally visible HTTP/SSE behavior using `create_app`
and dependency overrides, with deterministic authenticated users and test
dependencies. Assert the public response/error/event shape and the absence of
ADK or storage internals, not prompt wording. `tests/contract/` checks the
implemented OpenAPI surface. Agent-quality and prompt behavior evaluations
belong under `evals/bike-doc`, outside the service test suite.

When adding a seam, expose a small protocol at the service or runner boundary
and fake that protocol in unit tests. The important contract tests are the
public error envelope, owner-scoped behavior, turn and artifact idempotency,
event cursor/replay/SSE formatting, report and safety validation, and public
schema/OpenAPI alignment.

## Related documentation

- [Backend shape and layer rules](../../docs/specs/apps/api.md)
- [Public OpenAPI contract](../../docs/specs/openapi.yaml) and [error mapping](../../docs/specs/apps/api-errors.md)
- [Authentication boundary](../../docs/specs/apps/api-auth-dev.md) and [testing conventions](../../docs/specs/apps/api-testing.md)
- [Diagnostic API workflow](../../docs/specs/apps/api-diagnostic.md), [event/SSE semantics](../../docs/specs/apps/api-events-diagnostic.md), and [diagnostic persistence](../../docs/specs/apps/api-db-diagnostic.md)
- [Artifact storage boundary](../../docs/specs/apps/api-artifacts-diagnostic.md), [report schema](../../docs/specs/apps/diagnostic-report-v1.md), and [safety rules](../../docs/specs/apps/safety-diagnostic.md)
- [ADK tool contracts](../../docs/specs/apps/adk-diagnostic-tools.md), [ADK wiring](../../docs/specs/apps/adk-wiring-spec.md), and the package-local [ADK architecture](src/bike_doc_api/adk/ARCHITECTURE.md)
