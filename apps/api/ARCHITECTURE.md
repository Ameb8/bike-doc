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
| `main.py`, `core/` | App construction, settings, logging, process telemetry lifecycle, security primitives, and the public error envelope | `create_app`, `Settings`, `initialize_telemetry`, `install_exception_handlers` |
| `core/nats.py`, `core/event_notifications.py`, `core/event_wakeups.py` | Reusable asynchronous NATS connection lifecycle, V1 JetStream topology verification, one process-owned Core NATS event-hint subscription, and bounded local fanout | `nats_connection`, `NatsEventNotifications`, `EventWakeups` |
| `workers/` | Shared pull execution and typed registry; independently runnable profile and diagnostic process resources and application composition | `PullWorker`, `profile_registry`, `diagnostic_registry`, workload roles |
| `maintenance/` | API-hosted publication, policy-gated recovery and retention cleanup; finite operator reconstruction over PostgreSQL truth and pinned topology | `JobMaintenance.start/close`, `create_job_maintenance`, `recovery_policies`, `reconstruct` |
| `api/` | HTTP/SSE adaptation and dependency composition | `api/router.py`, `api/deps.py`, `api/v1/` |
| `schemas/` | Pydantic public request, response, event, and report shapes | Model conversion helpers beside each schema |
| `services/` | Product rules, ownership checks, workflow state, idempotency, and transaction-level coordination | `TurnService`, `DiagnosticVisualContextService`, `EventService` |
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
  validates artifact-storage configuration, configures logging, installs
  request-correlation/access logging middleware and error handlers, CORS, and
  the `/v1` router.
- [`api/router.py`](src/bike_doc_api/api/router.py) assembles versioned public
  route modules. `api/v1/` is the place to add a public endpoint group.
- [`api/deps.py`](src/bike_doc_api/api/deps.py) supplies request-scoped database
  sessions and authenticated users, configured providers, and the lifespan-owned
  PostgreSQL ADK session service.
- [`adk/background.py`](src/bike_doc_api/adk/background.py) is the diagnostic
  legacy composition root invoked after a legacy-selected turn is accepted.
  [`workers/diagnostic_role.py`](src/bike_doc_api/workers/diagnostic_role.py)
  hosts external execution for queue-selected turns.
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

Every event repository write registers its session-scoped highest sequence for
the repair session. A SQLAlchemy commit hook releases one hint after a successful
commit; rollback discards it. The hint contains only version, opaque repair
session ID, and sequence. The API lifespan owns one reconnecting Core NATS
subscription and local fanout for all SSE readers. Readers query PostgreSQL
after every hint and every bounded polling interval, including immediately
after subscribing to close the replay race. Hints never become SSE frames.
Heartbeats remain persisted events but do not publish hints, so multiple open
readers cannot trigger a heartbeat notification loop. NATS unavailability
does not gate API startup or event commits.

### Diagnostic turn

`POST /v1/repair-sessions/{sessionId}/turns` deliberately separates fast,
durable acceptance from model execution:

```text
turn route
  -> TurnService accepts, locks, validates, and persists turn.started
  -> durable_queue: atomically record diagnostic and eligible profile jobs
     -> API maintenance publishes -> separate workload workers
  -> legacy: FastAPI BackgroundTasks -> adk/background
  -> DiagnosticTurnOrchestrator prepares current-turn visual context, seeds durable context, and streams DiagnosticRunner
  -> ADK tools call services; runner events become public events
  -> EventService persists/commits events, then local and Core NATS wake-ups
```

`TurnService` validates session ownership and diagnostic state, validates
referenced artifacts, and enforces client-turn idempotency using a canonical
request hash. It creates or resumes one diagnostic phase session, persists the
turn and its `turn.started` event together, changes the repair session to
running, and commits before returning `202 Accepted`. An idempotent replay does
not start a second execution, even after a deployment selection changes. Its
acceptance cursor and timestamp remain anchored to the immutable turn rather
than subsequent worker or heartbeat updates.
`Settings.diagnostic_execution` defaults to `legacy`; `durable_queue` commits a
strict turn-ID-only diagnostic job and, for validated image evidence, an
independent pinned profile job in that same transaction. Job presence preserves
the executor selection. The route schedules neither legacy function for those
turns, including when `profile_inference_execution=legacy`. No route publishes
JetStream messages or calls ADK session storage.

Turn acceptance creates or reuses only the app-owned phase reference, with a
null ADK binding for new rows. The executor ensures and binds durable ADK state
before orchestration; bound identities are resumed exactly. The external queue
executor persists its fenced effect boundary afterward, as described below.
The ADK session service is lifespan-owned and
PostgreSQL-backed; see the [ADK architecture](src/bike_doc_api/adk/ARCHITECTURE.md)
for migration, startup validation, missing-session, and retention rules.

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
inference uses a separate workload consumer for queue-selected turns and may
commit before or after diagnostics. Diagnostic context includes inferred facts
only when already committed; profile state never gates diagnostic eligibility.
Legacy profile inference remains scheduled after legacy diagnostics.

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
sequence order. SSE formatting lives in the event service, not the route.
Committed event writes trigger local fan-out and a best-effort Core NATS hint;
other API processes receive the hint and query PostgreSQL for new events.
Bounded polling recovers missed hints, and hint failure does not roll back an
event. The durable `repair_session_events` log is the SSE source of truth and
reconnect mechanism. ADK sessions are also stored in PostgreSQL; the runner
returns a recoverable error if a bound ADK session is confirmed missing.

## Module reference

### `api/`

`api/` is the transport boundary. `router.py` joins route modules for auth,
bikes, artifacts, repair sessions, turns, events, decisions, and reports.
Route modules may use `Depends`, request/response types, headers, SSE response
formatting, and service factories. Keep them thin: validate transport input,
resolve dependencies, call a service, and map its result. Add reusable wiring
to `deps.py`; do not parse bearer tokens in individual routes or create ad hoc
database sessions.

Route decorators publish stable canonical operation IDs and use
`api/responses.py` for the shared `ErrorResponse` descriptions. The event route
advertises `text/event-stream` and the public event vocabulary. Contract tests
resolve local schema/response references and check media types, request
constraints, required fields, and permitted values across the diagnostic slice;
implemented routes have no expected-failure exemption. Existing owner-scoped
listing and pagination errors are recorded explicitly in that gate.

### `schemas/`

This package defines the public Pydantic V2 contract independently from
SQLAlchemy and ADK. It contains the client-visible IDs, status/phase enums,
event payload validation, report envelope, and model-to-schema conversion
helpers. Public API changes begin here and in
[`docs/specs/openapi.yaml`](../../docs/specs/openapi.yaml), not in an ORM model.
Report payloads use a discriminator on `schema_version` with typed diagnostic,
planning, execution, and shop referral variants. Report schema annotations also
describe the existing conditional evidence/outcome validation and required
serialized currency fields. There is no unrestricted dictionary payload in the
public report schema.

`schemas/background_jobs.py` is a separate internal contract: strict immutable
profile-inference instructions pin only turn ID and behavior versions. These
models are never exposed in the public API.

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

### Durable background job persistence

`services/background_jobs.py` validates the pinned `profile_inference.v1`
instruction and derives its logical identity from a canonical tuple hash.
`repositories/background_jobs.py` owns PostgreSQL job recording, exact-generation
publication claims and confirmation, atomic delivery resolution, token/deadline
fenced outcomes, and bounded locked maintenance scans. Its typed snapshots and
resolution outcomes are the publisher/runtime seam. Definition validation is a
pure synchronous callback under the delivery row lock; it must not perform I/O.

All operations use a caller-owned async transaction and never commit or publish.
Product services can record work with their existing writes; worker and
maintenance callers commit short transactions before transport settlement or
network calls. Scan results remain locked only for that transaction. Publication
claim tokens persist across the publisher's network call and expire independently
of execution tokens. `models/background_job.py` and Alembic revision `0009`
provide bounded state, immutable inputs, monotonic counters, terminal-outcome
protection, and scan indexes. Stored errors are fixed categories, never exception
messages. Expired deliveries wait for reconciliation; only maintenance advances
recovery publication intent. Retention scans exclude nonterminal and unconfirmed
jobs, and their configured cutoff must exceed broker lifetime and all longer
retry, recovery, and audit horizons.

This foundation does not register diagnostic jobs, publish messages, run handlers,
or change the current turn executor. Those integrations build on this seam.

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
validation for auth, artifact storage, model/provider credentials, and the
shared OTLP telemetry endpoint. `core/telemetry.py` is the only owner of
process-level OpenTelemetry providers, OTLP HTTP exporters, processors/readers,
and their bounded shutdown. The FastAPI lifespan starts that runtime before
routes can schedule background work and performs best-effort shutdown on exit.
In disabled mode it leaves the OpenTelemetry global providers untouched; in
enabled mode its one provider pair is shared with both BikeDoc and ADK.
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

## Durable job maintenance

`main.lifespan` starts `maintenance.hosting.create_job_maintenance` and cancels
it before closing the other API resources. `JobMaintenance.start()` immediately
schedules independent publication, reconciliation and cleanup loops; `close()` bounds
cancellation and publisher shutdown by `job_shutdown_timeout_seconds`. The
module has no FastAPI, route, ADK, or profile-provider dependency and can be
hosted through this lifecycle elsewhere after a deployment decision.

The publisher polls committed state even during NATS outages. A short
`BackgroundJobRepository.claim_publications` transaction commits an opaque token
and exact desired generation, then releases its session before broker I/O. A
bounded concurrent batch maps stored workloads through two fixed settings-backed
subjects and publishes only `version`, `job_id`, and `publication_generation`.
`Nats-Msg-Id` is `job_id:generation`. A JetStream acknowledgement precedes a
separate conditional confirmation transaction. Cancellation, failed confirmation,
and abandoned leases leave the generation republishable; an older confirmation
preserves newer intent. Transport failures retain a bounded error enum and a
future retry time with exponential equal jitter capped by settings. Backoff is
host-local efficiency state; PostgreSQL claims remain the correctness boundary.

The JetStream adapter connects and verifies the pinned topology lazily. NATS
connection failure cannot block lifespan startup or valid HTTP acceptance.
Routine failure logs contain fixed events/categories only. No route invokes
maintenance or publishes work directly. The existing background execution path
remains until its job-kind cutover tasks land.

Reconciliation uses bounded `SKIP LOCKED` scans and conditional updates inside
short transactions. Its policy registry is keyed by stored `(job_kind,
input_version)`; absent definitions are omitted at the database scan. Policies
make pure decisions about expired outcomes and safe no-progress notification
recovery. The API host registers profile safe replay and diagnostic boundary-aware
recovery, with atomic product terminalization described under #151. `ProfileJobRepository` couples
job transitions to existing inference-run audit state in that same transaction. Recovery of a retryable expired execution preserves attempts,
clears the expired token, advances durable generation, and schedules publication
at the policy's `eligible_at`. Exhaustion terminalizes as dead. No-progress
recovery advances only eligible queued/retrying work with remaining attempts
and no effect boundary. Publication remains the sole JetStream writer.

Settings and their timing constraints are documented in the root `.env.example`
and passed explicitly by Compose. `task test:maintenance` verifies real
PostgreSQL/JetStream acknowledgement-window replay, deterministic deduplication,
exact generation/coalescing, abandoned leases, two-host publication and recovery,
and policy/eligibility/deadline/attempt omissions with independent sessions.

## Shared pull workers

`workers/registry.py` defines the strict V1 delivery envelope, immutable typed
`ClaimedJob[Input]`, and exact `(kind, input_version)` allowlist. Definitions
supply the strict immutable input model, workload, async handler, application
attempt limit, maximum retry delay, effect policy, hard duration, cancellation
grace, settlement reserve, explicit timeout outcome, and reconciliation policy.
Registry construction rejects duplicate or incompatible definitions. Every
replica sharing a consumer must receive the complete supported registry;
deploy new definitions before enabling producers, retaining old versions through
all possible redeliveries. Database strings never become imports or commands.

`workers/runtime.py` owns one workload's bounded pull lifecycle through
`JobStore` and `PullTransport` protocols. PostgreSQL atomically validates and
claims authoritative input before a handler runs. Only an executable resolution
increments an application attempt. Terminal/stale deliveries acknowledge;
early/running deliveries receive a delayed NAK from authoritative eligibility
or deadline; expired running deliveries wait for API-hosted reconciliation.
Untrusted deliveries terminate without mutation; trusted definition/input
failures commit `dead` before termination. Exhaustion commits `dead` and
acknowledges. Handlers return bounded outcomes and never receive broker types.

`workers/adapters.py` supplies short, independent PostgreSQL transactions and
the official NATS delivery adapter. `apply_outcome_and_load` returns actual
committed eligibility/state, including attempt exhaustion, so the runtime
settles from durable state rather than guessing from a handler result. An ack
failure leaves safe redelivery. Progress affects only the broker AckWait;
the execution deadline cannot renew. Timeout starts cancellation before the
grace and settlement reserve. A cancellation-resistant handler or failed/stale
outcome persistence leaves the message unsettled for deadline recovery;
protected effects must still enforce the execution token and deadline. A handler
that resists cancellation retains its local concurrency slot until it finishes.
Maximum-delivery and termination advisory subscriptions log fixed categories
only, with no access to job state or message content.

`workers/role.py:create_workload_worker` is the thin workload-role seam. The
process host constructs and owns the database pool, NATS connection and durable
pull subscription, providers, and any ADK resources, then supplies its complete
registry and adapters. Both profile and diagnostic roles reuse this same
runtime. Dependency direction is `worker role -> registry/runtime/adapters`,
`runtime -> typed job store/transport protocols`, `database adapter -> job
repository`, and `NATS adapter -> core/nats + official client`. Registry and
handler interfaces import no NATS, FastAPI, providers, or ADK. A diagnostic
store/handler uses the fencing described below; atomic failure terminalization
remains #151 work. The profile role supplies its own isolated extractor and storage.

The initial role defaults are concurrency/fetch batch 4 (bounded by the
consumer's 128 pending acknowledgements), one-second fetches, ten-second
progress within the fixed thirty-second production AckWait, and thirty seconds
for in-flight shutdown. Timing is validated at worker construction. Provider
calls must fit the handler duration and use the single registered attempt
budget, without nested retries. Shutdown stops fetching, permits bounded
completion, cancels remaining work, then unsubscribes and drains NATS; forced
loss leaves running jobs and unacknowledged deliveries for recovery.

Run `task test:worker` after `task format` and `task check`. The script creates
disposable PostgreSQL 16 and pinned NATS 2.12.1 containers, migrates the database,
and runs `tests/integration/test_worker_runtime.py` with fake typed handlers and
real job rows on the profile workload consumer. It verifies pull, progress,
duplicate exclusion, delayed NAK, early/stale/future/malformed deliveries,
strict stored-input failures, confirmed acknowledgement, dropped acknowledgements,
timeout, graceful drain, and forced-loss redelivery. Containers and volumes are
removed on exit; no provider credentials are needed.

Issue #145 acceptance evidence (unit tests are in
`tests/unit/test_shared_worker.py`; real infrastructure tests are in
`tests/integration/test_worker_runtime.py`):

| Acceptance contract | Implementation and verification |
| --- | --- |
| Strict minimal envelope | `DeliveryEnvelope.decode`; strict-envelope and ambiguous/non-object JSON unit cases, real malformed termination |
| Fail-closed exact registry and policies | `HandlerDefinition`, `HandlerPolicy`, `HandlerRegistry`; duplicate, missing-policy, workload, strict-input, and invalid-timing unit cases |
| Subject role and authoritative dispatch | `PostgresJobStore.resolve` delegates locked validation to the registry; real wrong-subject/workload and stored-definition cases |
| One attempt/handler and bounded duplicate NAK | Atomic repository claim; simultaneous independent PostgreSQL transactions invoke once, duplicate delivery waits through the hard deadline |
| Terminal/stale no-op; future/workload rejection | Resolution settlement matrix unit cases and real attempt-free branch cases; newer intent is untouched |
| Untrusted identity versus trusted permanent failure | Decode before store access; real missing identity and unknown-kind/version/invalid-input cases prove zero attempts and trusted `dead` state |
| Retry commit and earliest eligibility | `finish` returns committed eligibility; real retry/early delivery verifies no premature second invocation |
| Terminal commit before ack and safe lost ack | Typed outcome matrix and stale-token/commit-failure unit cases; real dropped ack redelivers as a terminal no-op |
| Non-renewable deadline, timeout grace, and progress | Monotonic execution budget plus cancellation and settlement reserves; deterministic timing unit test and real progress/timeout tests |
| Graceful and forced shutdown | Bounded close lifecycle; fake resistant-fetch/handler cases and real drain/forced-loss redelivery |
| Typed handlers and bounded outcomes | `ClaimedJob[Input]`, registered outcome validation; typed-handler and invalid-result/retry unit tests |
| Fakes and real transport evidence | Complete resolution matrix with fakes; real pull, delayed NAK, progress, confirmed ack, redelivery, advisory, and drain tests |
| Required verification commands | `task format`, `task check`, then `task test:worker` |

## External profile inference role

`workers/profile_role.py` owns a native async process, bounded PostgreSQL pool
(no overflow), NATS connection, profile durable subscription, structured provider,
artifact storage, signal handling, and resource shutdown. It constructs the
complete `profile_inference` workload registry in `workers/profile_inference.py`
and runs the shared `PullWorker`. No handler publishes or settles messages.
The API maintenance composition imports pure workload recovery policies;
it does not construct worker/provider resources.

`profile_inference.v1` resolves accepted turn/artifact references through the
existing application service. Schema `bike_profile_inference.v1` and extractor
`drivetrain-specifications.v1` are the supported pinned implementations; unknown
pins fail definition validation before a claim. Deploy overlapping supported
definitions before any future version producer change.

Queue attempts bypass the domain run's legacy running lease and provider retry
loop. Domain metadata records the generic application attempt, and SDK retries
are disabled. Provider errors and timeouts retry at a fixed bounded delay;
exhaustion commits a dead job and exhausted domain audit. Expired execution can
replay safely within the same attempt budget; committed completed/abstained
runs return their result without extracting or mutating again. There is no
profile effect boundary that prohibits replay. `ProfileJobRepository` is the
transactional audit adapter for runtime and reconciler transitions, including
failures before dispatch. Domain write phases verify the current job token and
deadline under a job-row lock before mutating and again before commit.

Only failures explicitly classified by profile inference produce a retry
outcome. An unexpected application exception is rolled back and propagated to
the shared runtime, which leaves the delivery unsettled; the existing hard
deadline and expired-execution policy then govern recovery. It is never relabeled
as a provider outage, and exception text is not copied into durable job state or
worker logs.

After extraction, bounded resolution retries reload ORM state after rollback
and retry only database resolution, including commit conflicts. Claims,
dispositions, resolutions, profile projection/revision, and completion commit
atomically. The run's unique identity remains the authoritative result.

The process accepts SIGINT/SIGTERM, stops fetching and uses the shared bounded
shutdown/drain lifecycle, then closes provider/storage transports and disposes
the database pool. Forced termination leaves authoritative deadline recovery.
See root README for invocation, timing settings and worker-first canary rollout.


## Atomic profile canary acceptance

`Settings.profile_inference_execution` is a deployment-only `legacy` (default) or
`durable_queue` selection. The turn route composes `BackgroundJobService` over the
same request session as turn/event/session repositories. In queue mode,
`TurnService` validates all image evidence, locks the repair session, records an
app-owned phase reference (null ADK binding until executor initialization), the
turn and `turn.started`, state updates, and the strict `profile_inference.v1` job
before committing once. Migration `0010` permits the unbound phase reference.
The job pins `bike_profile_inference.v1` and the configured extractor; the existing
canonical tuple hash and database unique key define its logical identity.

A committed job is the durable selection. `last_profile_job_recorded` is the
request-local scheduling result, set after recording intent. The route schedules
the legacy profile function only for a new eligible acceptance without a job;
it schedules the existing diagnostic function independently. Idempotent replay
exits before selecting any executor, including after configuration changes.
Neither acceptance nor route constructs broker messages or opens NATS.

All turn acceptance modes use `DiagnosticPhaseSessionManager` under the
repair-session lock to create or reuse an app-owned phase reference. Its ID,
phase uniqueness, status, and report-schema snapshot belong to the acceptance
transaction; a new reference has null `adk_session_id`. The manager performs
no ADK I/O or commit, and the route no longer resolves an ADK client.

Before orchestration, the legacy diagnostic background executor invokes
`DiagnosticADKSessionClient.ensure_or_resume` in its own product transaction.
For null references it ensures `adk_diagnostic_{phase_session_id}` in the fixed
`bike_doc_diagnostic` / `backend_system` namespace, then uses the repository's
null-only compare-and-set. ADK commits independently of the product transaction.
Concurrent creators confirm the durable winning identity; concurrent binding
resumes the winner without replacement. A crash before the binding commit
repeats the same deterministic ensure. Non-null bindings always resume exactly
and confirmed missing state raises the pre-orchestration failure seam, never
recreates conversation state. The legacy host rolls back failed initialization
and emits its existing safe setup-failure events. Neither ADK IDs nor stored
state enter public responses/events or routine telemetry.

Migration `0011` indexes only non-null bindings, preserving the nonblank CHECK.
Its downgrade restores `0010`'s nullable full unique index; downgrading `0010`
with retained uninitialized references fails rather than discarding data.
`DiagnosticTurnInputV1` extends the existing internal job-input module with
exactly one strict ULID `turn_id`. No diagnostic producer, handler registration,
effect fencing, or executor selection is enabled by this change.

`task test:profile-canary` runs the integrated real PostgreSQL/JetStream matrix
with fake extraction. Profile tests now create their jobs through turn acceptance,
then exercise the real profile handler and shared runtime. Maintenance/runtime
suites supply the remaining transport and lifecycle injections. README documents
expand-first deployment, the worker readiness event, exactly-one-executor
selection and rollback preserving accepted queue work.

Issue #147 acceptance evidence:

| Criterion | Implementation and verification |
| --- | --- |
| Atomic turn/event/job/pins/intent and rollback | `TurnService` with one request transaction; profile fixture checks the committed rows and null phase binding; rollback tests cover commit loss, invalid pin and invalid artifact with no phase/turn/event/job intent left behind |
| Acceptance without NATS; later publication | Real HTTP outage canary stops NATS, receives `202`, then runs `JobMaintenance` and the profile worker after restart |
| Idempotent and concurrent acceptance | API replay tests switch selection in both directions; real same-key concurrent acceptance yields one turn/job/event; distinct-key concurrency cannot bypass running-session validation |
| Ineligible and legacy work | Unit/API image and text selection tests; existing invalid-artifact and legacy profile tests remain active |
| Exactly one executor; no route publication | API scheduling spies and real outage route prove queue work skips the legacy profile function while diagnostic work is scheduled; route imports only the recorder, never NATS |
| Worker-first compatibility/readiness | Idle real profile worker constructed with legacy producer selection; `profile_worker_ready` process event; README expansion sequence |
| One authoritative run/effects through loss and restart | Accepted-turn crash/effect tests, acknowledgement-window/broker/worker restart canary, and independent SIGKILL/restart/SIGTERM worker test with real PostgreSQL/JetStream and fake provider |
| Stale/future/subject/malformed/unknown definitions | Real shared-runtime fail-closed matrix plus profile unsupported-pin tests; no provider invocation or attempt for rejected definitions |
| Duplicate/retry/timeout/ack/shutdown/reconciliation/budget | Real maintenance/runtime matrix and accepted-turn profile timeout, resistant provider, exhaustion, ambiguous loss and external-process tests |
| Workload isolation | Profile pull-loop test inspects the untouched diagnostic durable consumer separately from job/run/profile rows |
| Unchanged HTTP/events; runnable diagnostic path | Existing API/contract/event tests; real deferred-binding diagnostic executor test resumes initialization after simulated binding loss |
| Required checks and integrated canary | `task format`, `task check`, then `task test:profile-canary`; the latter runs the real maintenance/runtime/profile suites and PostgreSQL job persistence tests |

This canary creates no `diagnostic_turn` job, introduces no profile dependency
for diagnostic eligibility, and retains the diagnostic background executor.
Monitoring, broker reconstruction, cleanup and load certification remain outside
this Task's scope.

### Deferred diagnostic sessions (#148)

The task's acceptance evidence is mapped below. `task test:sessions` runs the
real PostgreSQL tests, durable ADK compatibility tests, and phase persistence
repository tests in disposable storage. Model/provider work is faked; ADK
storage, SQL constraints, binding transactions, migrations, and child-process
resume are real.

| Acceptance criterion | Implementation and evidence |
| --- | --- |
| Strict `diagnostic_turn.v1` input | `DiagnosticTurnInputV1` in the existing internal `schemas/background_jobs.py`; strict-input tests accept one ULID and reject missing, blank, malformed, non-string, and additional fields |
| Nullable binding, partial uniqueness, nonblank validation, safe downgrade | Existing migration `0010` plus `0011` and matching model index; model metadata test and real migration roundtrip test cover multiple nulls, duplicate non-null rejection, blank rejection, restoration of `0010`, refusal to discard null references, and upgrade/downgrade/upgrade through `0009` |
| App identity and report snapshot without ADK | Manager unit tests and real acceptance tests preserve the phase ID, selected report schema, and existing phase row; new rows remain null |
| Acceptance independent of unavailable ADK and atomic phase creation | Route composition has no ADK dependency; real acceptance tests reject any attempted ADK access in both profile modes, and commit-loss tests prove no phase, turn, event, or running-state change survives rollback |
| Concurrent deterministic ensure and null-only stable binding | Independently constructed PostgreSQL ADK clients and product sessions initialize concurrently; repository winner test proves an existing binding cannot be replaced |
| Crash after ADK creation before binding/commit | Failure injection at both points followed by initialization in a child process derives the same identity and leaves exactly one durable ADK session |
| Exact bound resume across processes | A second independently constructed child process resumes the committed binding; existing ADK compatibility tests also verify durable state and event preservation |
| Missing binding fails closed before effects | Repository/client tests leave a confirmed missing identity unchanged with no replacement state; legacy integration test verifies orchestration is not invoked again and existing safe setup-failure events restore `awaiting_user` |
| Runnable legacy executor and unchanged public contracts | Background composition uses the shared ensure-or-resume; unit/API/contract tests and real legacy integration cover scheduling, orchestration, public `202`, and ordered persisted events; the profile canary's diagnostic compatibility test covers its existing deferred reference |
| Private ADK IDs/state and safe telemetry | Public response/event assertions and existing ADK storage/runner privacy tests cover internal identity, stored state, model content, and routine telemetry; the new failure path logs only the existing bounded event name |
| Focused and standard verification | Strict-input, model, session-manager, background, turn-service, API, real migration/repository/cross-process suites, `task format`, and `task check` |

This task does not record or register diagnostic jobs, change diagnostic executor
selection, start a diagnostic worker, or implement effect fencing/retry policy.

## Diagnostic worker composition (#149)

`adk/execution.py:build_diagnostic_orchestrator` is the single diagnostic graph.
The legacy `adk/background.py` host and `workers/diagnostic_application.py` both
compose it with a fresh executor-owned product session. `DiagnosticResources`
contains only process transports: durable ADK sessions, storage, observation
extraction, optional cost estimation, and an optional model. Neither worker
composition nor the graph imports route handlers or FastAPI dependency objects.
The legacy host adapts its existing API-lifespan resources to this same graph.

`workers/diagnostic.py` registers exactly `diagnostic_turn` / input version 1
(`diagnostic_turn.v1`) in the complete `diagnostic` workload allowlist. Its typed
handler receives `ClaimedJob[DiagnosticTurnInputV1]`, including identity, attempt,
execution token and deadline; transport settlement stays in the shared runtime.
The input contains only `turn_id`. At execution, the application loads the turn,
its repair/phase references and owner from PostgreSQL, ensures/resumes the exact
durable ADK binding, commits it, then calls the shared orchestrator. Accepted
text/artifact references come from the turn row. The orchestrator snapshots them
and reads current resolved bike profile and the existing repair-history service
at execution, preserving independent profile-inference timing.

Known transient preparation failures alone return bounded retry categories.
Confirmed missing bound ADK state returns a permanent pre-effect failure without
orchestration. Handled runner errors return job success after the existing
orchestrator durably settles the public turn; cancellation and stale execution
propagate. Unknown failures request a permanent outcome; the repository uses the
authoritative boundary to classify unhandled reserved execution as interrupted. Model errors are redacted before entering dependency telemetry. Optional pricing
lifecycle summaries omit requirement/search/listing text and raw exception traces, and
all completion logs/metrics reuse the bounded diagnostic telemetry contract.

`workers/diagnostic_role.py` owns the product pool (no overflow), NATS connection,
diagnostic durable subscription, advisory observation, complete registry and
shared runtime. `adk/resources.py` owns a separately bounded PostgreSQL ADK pool,
SDK model, storage and optional provider clients. It closes partial startup
resources and uses bounded shutdown after runtime drain. Committed event hints
reuse that same NATS connection via `NatsEventPublisher`; polling remains the
fallback. Dependency direction is `diagnostic role -> resources/application ->
shared diagnostic composition -> orchestration/services`, and `role -> shared
registry/runtime/adapters`. No worker copies pull/claim/progress/retry/settlement
or shutdown algorithms.

Effect-token enforcement is provided by #150 below, and #151 supplies atomic
product repair and registers expired-execution recovery. Runtime timeouts
request a classified pre-boundary retry; persistence rejects that outcome after
the boundary so deadline recovery terminalizes the reserved execution without
replay. The #152 producer canary
uses `Settings.diagnostic_execution` to select this role for newly accepted
turns, as described in the diagnostic turn path above.

| #149 acceptance | Evidence |
| --- | --- |
| Exact allowlist and complete policy | `diagnostic_registry`, `diagnostic_policy`; exact dispatch, duplicate/workload/missing-policy and invalid input/attempt tests |
| Claimed typed input and immutable instruction | Handler tests inspect all claimed fields; real seeded role loads accepted text/artifacts through the turn |
| Current supporting context | Shared orchestrator seed lookups; real test changes the bike after acceptance and verifies current ADK context |
| Durable precondition before orchestration | Preparation-order tests; real deterministic binding and confirmed missing-binding rejection |
| Shared legacy/queue graph | Existing background composition test now spies on `execution.py`; real role uses that graph and actual normalized ADK runner |
| No handler transport authority | `diagnostic.py` imports only typed registry/application contracts and bounded outcomes |
| Complete thin resource-owning role | Fake process/pool/NATS lifecycle tests and real PostgreSQL/JetStream diagnostic durable consumer |
| Typed timing/concurrency/pool validation | Invalid provider/handler/hard/grace/shutdown/fetch/pool/progress/attempt configuration tests; `.env.example` and Compose invocation |
| Cancellation propagation | Handler cancellation/unknown-error tests; shared runtime timeout/shutdown tests remain active |
| Bounded content-safe outcomes and telemetry | Model error redaction and provider failure tests; real test checks accepted content, provider exception text and artifact paths are absent from logs |
| Verification | `task format`, `task check`, `task test:diagnostic`; existing ADK orchestration/runner/service suites stay active |

## Diagnostic execution fencing (#150)

`core/diagnostic_execution.py` defines the frozen, transport-neutral
`DiagnosticExecution(job_id, token, deadline)` and the bounded
`StaleDiagnosticExecutionError`. Tokens are excluded from representations. The
claimed worker constructs this context after authoritative preparation and
ADK ensure/resume. Immediately before composing/invoking orchestration,
`repositories/diagnostic_execution.py:DiagnosticExecutionFence.cross_boundary`
locks the job, checks the current token and database wall-clock deadline, and
conditionally persists the irreversible boundary. Migration `0012` retains
`effect_boundary_token`, so a new current token cannot inherit an earlier
reservation. Repeating the crossing for its original current token is
idempotent; neither crossing nor subsequent checks changes the deadline.
Existing boundaries without a recorded owner fail closed.

The shared graph binds this authority to its executor-owned product session.
Every service and repository in that graph uses this same session, including
input-request, safety, report, event, session-completion and visual-extraction
operations. Session-local SQLAlchemy hooks lock/check the job before ORM SQL
(including direct DML), before autoflush/flush, after flush, and before commit.
This is a transactional compare-and-write: the job lock is held through the
product mutation and commit, so replacement cannot interleave. Clock checks
happen after lock acquisition, use `clock_timestamp()` rather than transaction
start time, and run again at commit to roll back writes flushed before expiry.
The lock order is job then product rows. A rejection remains sticky for the
execution, and service rollback discards its pending mutations and SSE hints.
Any future diagnostic service must reuse this fenced session; creating an
independent product session would bypass its authority and is prohibited.

```text
claimed handler -> DiagnosticExecutionApplication -> DiagnosticExecutionFence
                                                      -> product session hooks
                -> shared diagnostic graph -> orchestrator / runner / tools
                                           -> services -> repositories
                                                        -> same fenced session
```

Services and tools import no JetStream or worker types. Orchestration, the
runner request, and tool composition carry the same private execution context;
tool context uses a Pydantic private attribute. Explicit runner content/state
mapping omits it. It never enters public schemas, ADK state/model input, or
broker envelopes. Ordinary HTTP and legacy graph sessions are not bound to a
queue token. The route uses acceptance's committed job selection to schedule
exactly one executor; an idempotent replay schedules neither executor.

The committed whole-turn boundary is the durable fenced reservation for
external work: ADK invocation and its independent session writes, model calls,
observation extraction, storage reads, and optional report pricing. No recovered
execution may repeat this reservation. Before runner/model/extraction
invocation, the graph rechecks authority and commits pending visual attempt or
agent-start markers. Report pricing checks authority while retaining its
existing product-row locks and validated state. Storage reads are bounded,
read-only operations under the already-committed reservation. The boundary
does not promise exactly-once calls: an in-flight external call may continue
after cancellation/expiry, but its late product result is transactionally
rejected. No cancellation assumption or renewable lease underpins safety.
JetStream progress continues to extend acknowledgement time only.

Stale authority propagates through services, tool normalization, model adapter,
runner and orchestration without generating a public error/completion pair.
The shared runtime emits only `job_worker_execution_stale` and leaves delivery
unsettled; #151 owns the recovery/product-terminalization transaction.

| #150 acceptance | Implementation and evidence |
| --- | --- |
| Valid, unexpired boundary after phase initialization | Worker preparation-order unit test; real missing-binding test; locked conditional crossing |
| Single boundary, same-owner idempotence, immutable deadline | Independent-session boundary race, repeat-crossing and replacement-owner tests |
| Every protected write family | Session SQL/flush/commit hook unit tests; service stale-error unit tests; PostgreSQL replacement/expiry matrix for events, input, safety, report, session DML, completion, visual run/manifest/attempt/result/failure/start/redaction |
| Expiry despite a live process | Real wall-clock lock-wait test and rollback of a previously flushed event; no replacement needed |
| Safe stale rejection and external reservation | Private-context unit test; real in-flight model loses token, progress cannot renew deadline, product snapshot remains unchanged and delivery remains unacknowledged |
| Existing product contracts | Existing orchestration, tool, event, safety, report, visual and public-contract suites; real seeded valid/handled-error role paths |
| Required verification | `task format`, `task check`, `task test:diagnostic` |

### Diagnostic terminal outcomes (#151)

`repositories/diagnostic_terminalization.py:append_diagnostic_completion` owns
the validated completion/error writes shared by legacy setup, orchestration, and
job recovery. `repositories/diagnostic_jobs.py:DiagnosticJobRepository` composes the generic
transition hook with diagnostic product terminalization and the existing profile
lifecycle hook. The diagnostic worker uses it for outcome and permanent-definition
resolution; API maintenance uses it for both registered workloads. Every terminal
repair holds the job lock before the repair-session lock, validates the stored
turn reference, and commits the job, `awaiting_user` state, validated error, and
immediately following completion snapshot together. Terminal job state guards
repeated delivery/reconciliation. Malformed or missing references cannot authorize
product mutation.

`DiagnosticRecoveryPolicy` retries lost pre-boundary execution within the persisted
attempt budget, exhausting to `dead`; reserved execution becomes `interrupted`
without advancing publication. The generic scan and publication CAS preserve
eligibility, attempts, and active deadlines. Preparation classifies only explicit
connection/timeout and transient PostgreSQL failures as retryable; other preparation
exceptions are permanent. Unknown handler failures never retry: the repository
maps them to dead before the boundary and interrupted after it. Handled
orchestration errors commit their public pair
together and return job success. If product completion committed before job
settlement was lost, the hook recognizes it as success and preserves the current
session snapshot, including a newer accepted turn. Post-commit event notification
hooks remain the only cross-process wake-up path. Public retryability authorizes a
new user turn; it cannot revive the terminal job.

Real PostgreSQL/JetStream diagnostic tests cover rollback after completion flush,
competing and repeated recovery, exhaustion, invalid definitions, handled model
errors, completion-before-settlement loss, and terminal delivery without another
attempt or model invocation. The #152 canary composes these contracts with
atomic acceptance and route executor selection.

| #151 contract | Implementation and verification |
| --- | --- |
| Only classified pre-boundary retries, durable before delayed NAK | Preparation classifier, handler and generic outcome contract; unit classification tests and real `test_classified_retry_is_durable_before_real_delayed_nak` |
| Exhaustion/permanent failure repairs product and emits one ordered pair | `DiagnosticJobRepository` plus shared `append_diagnostic_completion`; pre-boundary outcomes and expired-attempt-budget integration tests |
| Trusted definition failure consumes no attempt; malformed identities do not mutate product | Transition hook validates a minimal turn reference from the trusted row; version/extra/malformed/missing definition integration matrix |
| Handled error succeeds, including loss after product commit | Atomic orchestration pair; seeded model-error and completion-before-settlement integration tests preserve newer session state |
| Expired post-boundary execution interrupts without replay | Recovery policy and conditional generic persistence; expired-boundary race test asserts unchanged generation and no model calls |
| Concurrent/repeated recovery cannot duplicate or reverse results | Job/session lock order, terminal-state guard and completion identity; rollback-after-flush, competing recovery and repeated maintenance tests |
| Notification recovery respects boundary, eligibility, attempts and active deadlines | Generic scan/CAS plus conservative diagnostic policy; diagnostic recovery tests and existing maintenance timing matrix |
| Succeeded/dead/interrupted deliveries acknowledge without attempts | Existing terminal resolution/ACK mapping; real broker terminal-redelivery and completion-before-settlement tests |
| Public retryability creates new work only | Terminal outcomes remain immutable under the existing database trigger; terminal-redelivery tests prove the original is never revived |
| Content-safe errors/telemetry and unchanged event validation/wake-ups | Fixed `JobError` values and public error text, shared schema validation and event repository commit hooks; privacy, public event and cross-process SSE compatibility suites |

The integrated diagnostic canary and worker-first enablement/drain/rollback
procedure are documented in [README.md](README.md#diagnostic-queue-canary).
Final removal of legacy background composition remains a later completion gate.

### Complete recovery and retention (#153)

`maintenance/hosting.py:recovery_policies` supplies the same complete initial
kind/version allowlist to API reconciliation and finite reconstruction. Expired
executions retain their kind-specific attempt/effect rules, with a typed recovery
backoff floor. The repository invalidates execution tokens while holding the job
lock, then atomically persists any diagnostic/product repair before eligibility.

`maintenance/runtime.py` also hosts bounded terminal cleanup in FastAPI lifespan.
`BackgroundJobRepository.delete_retained` rechecks terminal state, confirmed
intent, absence of publication claims, terminal age and last-update age under
the row lock. Publication confirmation restarts retention, so delayed terminal
notifications cannot outlive their resolvable rows. Cleanup has no broker-state
dependency. Scan locks live only through short transactions and release on
cancellation; publication leases naturally expire.

`maintenance/reconstruction.py` is an explicit finite operator composition over
`core/nats.py` topology validation and repository keyset scans/conditional intent
advances. It starts no runtime and sends no work messages. Acceptance cutoff,
bounded page transactions, pending-intent coalescing and generation CAS permit
interruption, restart and concurrent API maintenance. Unknown definitions,
terminal jobs, future eligibility, exhausted attempts, active executions and
reserved diagnostic effects cannot become executable through reconstruction.
The pinned stream now has finite MaxAge; typed retention exceeds transport,
retry/recovery and audit horizons. Stable recovery events contain only registered
kind/outcome and counts/booleans. Advisories remain observational.

See [QUEUE_RECOVERY.md](QUEUE_RECOVERY.md) for the operator procedure,
configuration relationships, compatibility migration and acceptance evidence.
`task test:recovery` runs real maintenance, both workload suites, pinned topology
contracts and persistence races in disposable PostgreSQL/JetStream.

### Queue inspection and observation (#154)

`operations/inspection.py` composes a finite read-only PostgreSQL projection from
`repositories/job_inspection.py` and optional NATS observations. It is an internal
command, not a product endpoint. Projection excludes job input and tokens; a
read-only transaction protects every command path. PostgreSQL truth is always
separate from optional transport observations.

`core/queue_contract.py` defines stable names and exact finite dimension sets.
`core/queue_telemetry.py` implements the failure-isolated OpenTelemetry adapter
and deterministic recorder. Maintenance, shared workers and SSE use narrow
observation hooks; handlers and mutation repositories do not import exporters.
`db/pool_observation.py` samples safe driver-exposed pool counts. Both independent
workload programs use the process-wide telemetry lifecycle.

`operations/monitoring.py` owns read-only NATS administration and advisory
subscriptions, with no job transition or product-repair dependencies. API
maintenance owns its lifecycle; worker hosts reuse the adapter. No advisory can
feed authoritative recovery. See [QUEUE_OBSERVABILITY.md](QUEUE_OBSERVABILITY.md)
for commands, metric semantics, cardinality/privacy policy and real-infrastructure
verification via `task test:observability`.

### Production process contracts (#155)

`compose.production.yaml` hosts API-owned V1 maintenance, independent diagnostic
and profile roles, one pinned persistent JetStream server, migrated PostgreSQL,
a loopback HTTP gateway and an OTLP Collector. It is separate from development
Compose. `operations/deployment.py` loads only the mounted role secret, then
executes the established entry point with normal signal supervision. Settings
are validated before exec so malformed secrets produce a fixed error instead of
an import-time traceback containing Pydantic's environment inputs. Settings
`process_role` separates worker configuration from HTTP auth requirements; HTTP
construction explicitly requires the API role. Workers retain one shared runtime.

`operations/health.py` provides an opt-in process-local loopback listener on
`health_port`. It distinguishes process, database, broker, consumer and
worker/maintenance readiness without provider calls. Maintenance tracks successful
cycle freshness; workers expose fetch-loop liveness and the latest fetch result. These observations do not
mutate job state. Worker topology checks inspect only their existing durable and
monitoring subscriptions are workload-scoped; API/operator composition provisions
and verifies the complete topology. Broker permissions enforce the boundary.

`operations/alerts.py` validates and evaluates versioned backend-reduced alert
windows against `core/queue_contract.py`. Application metrics still use the one
OTLP instrumentation/export path. Infrastructure resource polling and alert
routing belong to the deployment backend. See [deployment runbook](../../deploy/README.md)
for security policy, secrets, health, shutdown, rollout/rollback, rotation,
backup/reconstruction and executable validation.
The deployment entrypoint scopes the configured `telemetry_service_name` by
container hostname (`<base>.<hostname>`), so replicas' cumulative counters and
connection pools remain distinguishable. The application resource retains
exactly the canonical three fields; no metric attribute or second instrumentation
path is added. Native entrypoints preserve their configured service name.
A timed-out pull with a disconnected client or recorded protocol/permission error
raises a bounded transport failure instead of masquerading as an idle successful
fetch. Existing runtime telemetry consequently marks fetch availability false;
reconnect clears the client's transport error. Readiness reports a connected
broker with a recorded permission error as unhealthy consumer access.
Read-only health connections use finite initial retries and immediate bounded
close, under a total probe deadline. Process-owned publisher/SSE/worker connections
retain unlimited automatic reconnect and graceful drain. Broker loss must produce
a boolean failed readiness response, not an indefinitely connecting probe.

### Measured workload budgets (#156)

API requests and SSE share one bounded `api` database pool. API-hosted
publication/reconciliation/monitoring use a separate bounded `maintenance`
pool so long SSE streams cannot occupy maintenance's connections. The existing
cached session factory normalizes defaults before caching to avoid accidentally
creating two API pools. ADK storage has a bounded, observed `adk` pool; worker
product pools remain independent. Every pool has zero overflow and bounded
checkout wait. Observations distinguish pool role within each process resource.

Consumer acknowledgement limits and waits are typed settings shared by topology
provisioning and role validation. Runtime progress cadence must remain below half
the broker wait; local concurrency must fit the consumer limit. Live consumer
configuration mismatches continue to fail closed. No deadline is renewed.

`tests/capacity/` is an opt-in certification composition over the production
Compose surface, product HTTP/SSE routes, existing worker factories and real
PostgreSQL/JetStream. Only external model/extractor adapters are deterministic
fakes. It records Task 4.2 signals and independent database/broker observations,
checks durable-effect uniqueness and operational privacy, and owns disposable
secrets/volumes. It is never imported by production or normal unit tests.
`deploy/capacity.json` pins reviewed role values used by secret generation;
unit tests keep them synchronized with typed defaults and `.env.example`.
See [CAPACITY.md](../../deploy/CAPACITY.md) for commands, budgets, results,
isolation limits, scaling arithmetic and alert implications.
