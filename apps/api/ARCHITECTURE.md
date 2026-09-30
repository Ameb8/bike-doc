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
| `workers/` | Shared pull execution and typed registry; independently runnable profile process resources and application composition | `PullWorker`, `profile_registry`, `profile_role` |
| `maintenance/` | API-hosted durable publication polling and bounded policy-gated reconciliation, with lazy JetStream resources and short independent database transactions | `JobMaintenance.start/close`, `create_job_maintenance`, `ReconciliationPolicy` |
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
  -> FastAPI BackgroundTasks
  -> adk/background builds a fresh service/repository graph
  -> DiagnosticTurnOrchestrator prepares current-turn visual context, seeds durable context, and streams DiagnosticRunner
  -> ADK tools call services; runner events become public events
  -> EventService persists/commits events, then local and Core NATS wake-ups
```

`TurnService` validates session ownership and diagnostic state, validates
referenced artifacts, and enforces client-turn idempotency using a canonical
request hash. It creates or resumes one diagnostic phase session, persists the
turn and its `turn.started` event together, changes the repair session to
running, and commits before returning `202 Accepted`. An idempotent replay does
not start a second background execution.

Today turn acceptance can create or resume the durable ADK session binding;
`repair_phase_sessions.adk_session_id` remains non-null. Chunk #137 will move
initialization to the worker with a nullable, idempotent ensure-and-bind step
and diagnostic effect fencing. The ADK session service is lifespan-owned and
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

### `schemas/`

This package defines the public Pydantic V2 contract independently from
SQLAlchemy and ADK. It contains the client-visible IDs, status/phase enums,
event payload validation, report envelope, and model-to-schema conversion
helpers. Public API changes begin here and in
[`docs/specs/openapi.yaml`](../../docs/specs/openapi.yaml), not in an ORM model.
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
schedules independent publication and reconciliation loops; `close()` bounds
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
recovery. The API host registers the profile safe-replay policy; diagnostic product
terminalization belongs to its cutover task. `ProfileJobRepository` couples
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
registry and adapters. Both profile and later diagnostic roles reuse this same
runtime. Dependency direction is `worker role -> registry/runtime/adapters`,
`runtime -> typed job store/transport protocols`, `database adapter -> job
repository`, and `NATS adapter -> core/nats + official client`. Registry and
handler interfaces import no NATS, FastAPI, providers, or ADK. A diagnostic
store/handler must add atomic product terminalization and effect fencing in
Chunk #137. The profile role supplies its own isolated extractor and storage.

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
The API maintenance composition imports only the pure profile recovery policy;
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

After extraction, bounded resolution retries reload ORM state after rollback
and retry only database resolution, including commit conflicts. Claims,
dispositions, resolutions, profile projection/revision, and completion commit
atomically. The run's unique identity remains the authoritative result.

The process accepts SIGINT/SIGTERM, stops fetching and uses the shared bounded
shutdown/drain lifecycle, then closes provider/storage transports and disposes
the database pool. Forced termination leaves authoritative deadline recovery.
This expands worker support without enabling any turn producer; selection and
canary cutover remain #147. See root README for invocation and timing settings.
