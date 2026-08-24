# BikeDoc Durable Background Job Queue Spec

Status: Canonical v1.2
Last updated: 2026-08-24

This document defines the canonical architecture and behavior for durable
background execution in the BikeDoc backend. Within this scope, it supersedes
the in-process FastAPI `BackgroundTasks` execution model described in
`docs/specs/apps/adk-wiring-spec.md`.

The public HTTP and event-stream contracts remain governed by
`docs/specs/openapi.yaml` and the feature-specific specs. This document is
authoritative for accepting, publishing, executing, retrying, recovering, and
operating asynchronous jobs.

## References

- Backend organization: `docs/specs/apps/api.md`
- Backend architecture: `apps/api/ARCHITECTURE.md`
- Diagnostic API behavior: `docs/specs/apps/api-diagnostic.md`
- Diagnostic persistence: `docs/specs/apps/api-db-diagnostic.md`
- Diagnostic events and SSE: `docs/specs/apps/api-events-diagnostic.md`
- Diagnostic telemetry: `docs/specs/apps/diagnostic-telemetry.md`
- Automatic bike profile inference:
  `docs/specs/apps/agent/bike-profile-inference.md`
- Backend testing: `docs/specs/apps/api-testing.md`

## Normative Language

The terms **must**, **must not**, **should**, **should not**, and **may** are
normative. “Must” and “must not” define required behavior. “Should” and
“should not” define the expected default unless a later canonical spec or ADR
records a justified exception.

## 1. Purpose

BikeDoc accepts work that can outlive the HTTP request that created it. That
work must not be lost when an API process exits, a worker restarts, or the
message broker is temporarily unavailable.

BikeDoc therefore uses a durable job queue composed of:

- PostgreSQL as the authoritative application job and job-input store;
- publication intent recorded on each PostgreSQL job row for atomic job
  acceptance;
- NATS JetStream as the durable execution-notification transport;
- the official asynchronous Python NATS client used directly, without Celery,
  Taskiq, or another distributed-task framework;
- a BikeDoc-owned asynchronous job runtime with typed job handlers;
- durable pull consumers grouped by workload class rather than job kind;
- durable Google ADK session storage; and
- cross-process wake-ups for persisted SSE events.

This design provides **at-least-once delivery**, not exactly-once execution.
JetStream owns durable transport, acknowledgement deadlines, redelivery, and
consumer state. PostgreSQL owns accepted intent, authoritative input,
eligibility, idempotency, effect safety, and product outcomes. The BikeDoc job
runtime maps between them through a small interface.

## 2. Scope

This spec covers:

- durable creation and publication of background jobs;
- job-row publication intent and confirmed publication generations;
- versioned job inputs stored in PostgreSQL;
- direct JetStream publication and durable pull consumption;
- the generic job-runtime and typed-handler interfaces;
- workload-class routing and independently scalable worker pools;
- acknowledgement, duplicate delivery, retry, and termination behavior;
- dependency ordering between diagnostic and profile-inference work;
- diagnostic effect boundaries and stale-execution protection;
- narrow reconciliation after process or broker failure;
- durable ADK state and cross-process diagnostic-event wake-ups;
- security, observability, testing, rollout, and recovery; and
- the architectural seams expected in the backend.

The initial job kinds are:

- `diagnostic_turn`; and
- `profile_inference`.

Many additional job kinds are expected. Adding a job kind must not require a
new message protocol, stream, consumer, worker deployment, or route integration
unless its workload characteristics justify separate isolation. Every job kind
must define its versioned input, idempotency, retry, effect-boundary, and
terminal-state rules.

## 3. Non-Goals

This spec does not:

- make model calls, external effects, or tool execution exactly once;
- make NATS or JetStream the source of product truth;
- place authoritative job inputs or user-authored content in broker messages;
- provide a task result backend separate from PostgreSQL;
- introduce Celery, Taskiq, Kombu, or a generic distributed-task protocol;
- create one stream, consumer, or worker deployment for every job kind;
- reimplement JetStream persistence, acknowledgement deadlines, or redelivery
  in PostgreSQL;
- make broker advisories or a dead-letter stream authoritative job state;
- move public diagnostic events out of PostgreSQL;
- change the public turn-acceptance or SSE contracts;
- define final SQL DDL, package names, timeout values, or capacity settings;
- define a general domain-event platform, CQRS, or event sourcing; or
- expose internal job identifiers through the public API.

## 4. Canonical Language

### Background Job

A **background job** is the authoritative PostgreSQL record of accepted work.
It identifies the job kind, workload class, versioned input, state, publication
generation, and bounded execution metadata needed for safe at-least-once
processing.

### Job Input

A **job input** is the immutable, versioned instruction accepted for one job.
It is stored in PostgreSQL as a small validated JSON object, stable domain
references, or both. Large artifacts remain in their authoritative stores.

### Publication Intent

A **publication intent** is the durable indication that a job generation must
be notified to JetStream. V1 stores it on the job row by keeping the desired
publication generation separate from the last confirmed generation. This
provides transactional-outbox semantics without a separate outbox table.

### Delivery

A **delivery** is one receipt of a JetStream message by a worker. Multiple
deliveries may refer to the same job generation and do not necessarily create
multiple application attempts.

### Workload Class

A **workload class** groups job kinds with compatible latency, resource,
security, and scaling characteristics. Subjects, durable consumers, and worker
pools are assigned by workload class, not by individual job kind.

### Job Handler

A **job handler** is the typed adapter for one job kind. It receives a claimed
job with validated input, invokes application behavior, and returns a bounded
outcome. It must not settle or publish JetStream messages.

### Job Runtime

The **job runtime** is the generic asynchronous module that consumes
deliveries, claims and loads PostgreSQL jobs, selects handlers, maintains
acknowledgement progress, applies outcomes, and settles messages.

### Execution Token

An **execution token** is a bounded, opaque value recorded when an eligible job
atomically moves into execution. Protected writes compare it with the job's
current token to reject stale execution.

### Effect Boundary

An **effect boundary** is the point after which replaying the whole job might
duplicate user-visible, durable, billable, or external effects. Every job kind
must define whether it has such a boundary and what recovery is allowed after
crossing it.

### Reconciliation

**Reconciliation** repairs gaps left by process failure, broker loss, delivery
exhaustion, or inconsistent application state. It is a safety net, not a
parallel queue or normal retry scheduler.

## 5. Decision Summary

| Area | Canonical decision |
| --- | --- |
| Job and product truth | PostgreSQL |
| Authoritative job input | Versioned PostgreSQL payload and/or domain references |
| Atomic acceptance | Publication generation recorded on the job row |
| Publisher runtime | One reusable async Python module; embedded in FastAPI for V1 |
| Broker | NATS JetStream |
| Client | Official async Python NATS client used directly |
| Worker framework | BikeDoc-owned async runtime; no Celery or Taskiq |
| Delivery guarantee | At least once |
| Broker payload | Version, opaque job ID, and publication generation only |
| Routing | Workload class, not job kind |
| Consumption | Durable pull consumers with explicit acknowledgement |
| Retry | Application classifies; JetStream delayed NAK performs normal redelivery |
| Duplicate handling | Atomic PostgreSQL claim and optional execution token |
| Dead work | PostgreSQL terminal state; broker advisories are hints |
| Diagnostic post-effect failure | Reconcile as interrupted; never replay automatically |
| ADK session state | Durable PostgreSQL-backed storage |
| SSE wake-up | Lossy hint with PostgreSQL replay and polling fallback |

JetStream provides notification transport and consumer mechanics. The runtime
provides only application-aware composition JetStream cannot infer. Job
handlers remain independent of transport semantics so new job kinds reuse the
same runtime safely.

## 6. System Invariants

1. When acceptance commits, product records, job records, immutable inputs, and
   required publication generations must commit atomically.
2. When acceptance rolls back, no executable publication intent may remain.
3. NATS availability must not be required to commit valid accepted work.
4. Messages identify PostgreSQL job generations and contain no authoritative
   inputs.
5. PostgreSQL determines job kind, workload class, input, eligibility,
   dependency state, and product outcome.
6. Every message may be delivered more than once.
7. Delivery of a completed or terminal job must become a safely settled no-op.
8. An atomic PostgreSQL transition must prevent concurrent duplicate
   execution.
9. A replaced execution token must reject later protected writes by stale work.
10. A diagnostic turn must not replay automatically after its effect boundary.
11. Positive acknowledgement must follow the durable outcome that makes
    removal of the delivery safe.
12. A crash after broker confirmation but before PostgreSQL confirmation may
    duplicate publication and must remain safe.
13. Broker loss must be recoverable by republishing eligible nonterminal jobs
    from PostgreSQL.
14. Messages, metrics, and routine logs must not contain user-authored content
    or secrets.
15. Workloads with materially different operational characteristics must be
    independently deployable and scalable.

## 7. Architecture

```text
                          PostgreSQL
                +---------------------------+
                | product records and events|
                | jobs and versioned inputs |
                | publication generations   |
                | durable ADK sessions      |
                +-------------+-------------+
                              ^
               atomic accept | claim and persist outcome
                              |
Client -> FastAPI API --------+-------- async job workers
             |                                 ^
             | API-owned publication loop     | durable pull
             v                                 |
        NATS JetStream ------------------------+

FastAPI SSE readers <--- lossy wake-up hint + PostgreSQL event replay
```

### 7.1 Module Responsibilities

FastAPI routes and application behavior validate and accept work. They create
jobs and publication intent in the same PostgreSQL transaction as the product
change. Correctness must not depend on direct publication from a route.

The background-job module owns job creation, input validation, publication
generation, execution eligibility, dependency release, effect-boundary policy,
outcome transitions, and reconciliation.

FastAPI application processes host the V1 publication loop. The loop is one
reusable asynchronous Python module, not route logic. It claims jobs with
unconfirmed desired generations, publishes through the JetStream adapter,
waits for a publish acknowledgement, and records confirmation. Multiple API
replicas may run the module safely. V1 does not require a separate dispatcher
deployment.

The publication module must also have a standalone process entry point that
uses the same implementation, configuration, PostgreSQL repository, JetStream
adapter, metrics, and shutdown behavior as the embedded lifecycle. Providing
that entry point does not create another V1 deployment; it preserves a clean
deployment seam if publication later needs independent scaling or availability.

JetStream owns stream storage, durable consumer state, acknowledgement
deadlines, redelivery, and delivery metadata. It does not decide product state
or whether a job may run.

The job runtime owns pull consumption, bounded concurrency, envelope
validation, atomic claim-and-load, handler selection, progress acknowledgements,
outcome persistence, and final settlement.

Job handlers own job-kind-specific behavior and return a bounded outcome such
as succeeded, retry after a delay, interrupted, dead, or cancelled. They must
not know how JetStream settlement works.

Reconciliation repairs abandoned publication claims, stranded job states,
satisfied dependencies not yet released, exhausted deliveries, and broker
reconstruction. It may initially run in the worker deployment. When recovery
requires another notification, it advances durable publication intent; the
FastAPI-owned publication loop remains the only JetStream job publisher.

### 7.2 Interfaces and Adapters

The backend should expose small internal interfaces for:

- recording a typed job within a caller-owned PostgreSQL transaction;
- claiming and loading an eligible job;
- applying a typed job outcome;
- publishing a minimal delivery envelope;
- executing a validated job through its registered handler; and
- notifying SSE readers of persisted events.

The JetStream implementation and test fake are adapters at the publication and
delivery seams. Transport-specific types must not leak into routes, product
behavior, unrelated repositories, or handlers.

The publication module's interface must own batching, claims, backoff,
acknowledgement, confirmation, and graceful shutdown behind a small lifecycle
surface. FastAPI startup and the standalone command may host that interface;
they must not duplicate its implementation.

Material implementation changes to module responsibilities and dependency
directions must also update `apps/api/ARCHITECTURE.md`.

## 8. Durable Acceptance and Dependencies

### 8.1 Turn Acceptance

For a newly accepted diagnostic turn, one PostgreSQL transaction must:

1. validate the request and current product state;
2. persist the accepted turn and its initial public event;
3. update the repair session as required by diagnostic specs;
4. create one executable `diagnostic_turn` job with versioned input;
5. set its desired publication generation ahead of its confirmed generation;
   and
6. when applicable, create a blocked `profile_inference` job with no
   publishable generation yet.

The transaction either commits all required records or none. HTTP idempotency
must return an existing acceptance without creating another logical job, with
a database uniqueness constraint providing the race-safe guarantee.

### 8.2 Producer Rule

The FastAPI backend is the sole V1 producer of application jobs, and its
processes host the sole V1 publisher module. Product code must record jobs
through the background-job interface rather than construct JetStream messages
directly. A long-lived publication coroutine in the FastAPI deployment is the
reliable publication path.

An opportunistic post-commit publish may reduce latency, but it must use the
same publication adapter and must not replace the durable publication loop.
Failure of that fast path must leave the committed job publishable.

### 8.3 Dependent Work

Profile inference triggered by a turn should be represented at original
acceptance time and depend on the diagnostic job.

When the dependency reaches an allowed terminal state, the same transaction
that releases the profile job must advance its desired publication generation.
Reconciliation must release a blocked job whose dependency already satisfies
policy.

The precise diagnostic states that release profile inference must be explicit
and deterministic rather than inferred from message acknowledgement.

## 9. Durable Job Record and Inputs

This section defines logical requirements, not final SQL DDL.

### 9.1 Generic Job Fields

Each job must retain enough information to determine:

- stable job and idempotency identities;
- registered job kind and workload class;
- input schema version;
- small immutable JSON input and/or stable domain references;
- any prerequisite job;
- current product-relevant state and earliest eligible time;
- bounded application attempt count and attempt limit;
- desired and last confirmed publication generations;
- publication eligibility, short dispatcher claim, confirmation time, and
  bounded transport error;
- execution start, conservative deadline, and execution token when required;
- whether and when its effect boundary was crossed;
- first start and terminal completion times; and
- latest bounded, redacted error category.

The lifecycle must distinguish blocked, queued, running, retrying, succeeded,
interrupted, dead, and cancelled work.

### 9.2 Versioned Job Inputs

Each job kind must register a strict, versioned input schema. Workers validate
stored input before invoking a handler. Unknown kinds or input versions fail
closed and become visible to reconciliation.

Inputs should use opaque identifiers when authoritative domain records already
exist. They should include an expected revision or immutable snapshot when a
later domain change must not alter the accepted instruction.

Large media, prompts, assistant output, credentials, and other sensitive or
unbounded values must not be copied into the generic job payload. They remain
in an authoritative database or object store and are loaded through stable
references after normal checks.

Adding a job kind should normally require only:

1. a versioned input model;
2. a registered handler;
3. explicit retry, effect-boundary, and terminal rules; and
4. assignment to an existing workload class.

### 9.3 Atomic Claim and Load

Eligibility, duplicate prevention, attempt increment, token creation, and input
loading should occur in one PostgreSQL operation where practical, such as an
atomic update with a returning clause. The claim result, not the message,
determines whether the delivery executes, waits, no-ops, or terminates.

### 9.4 Attempt Tracking

V1 must not add a generic attempt table solely to duplicate JetStream delivery
history. The job row may retain bounded attempt and execution information
needed for product behavior or recovery. A job kind may add domain-specific
attempt records when its audit or product requirements justify them.

### 9.5 When a Separate Outbox Is Required

Publication fields on the job row are canonical while each job has at most one
pending execution notification per generation and one destination.

A separate outbox requires an explicit design update and is appropriate when
one transaction must produce multiple independent messages, fan out to several
destinations, publish non-job integration events, or preserve publication
history independently of job retention.

## 10. Publication and JetStream Contract

### 10.1 Publication Loop

PostgreSQL and JetStream do not share a transaction. The publication loop must:

1. claim due jobs whose desired generation exceeds their confirmed generation;
2. derive an allowlisted subject from the stored workload class;
3. publish a minimal JSON envelope with a deterministic `Nats-Msg-Id` based on
   job ID and generation;
4. require a JetStream publish acknowledgement;
5. record the confirmed generation in PostgreSQL; and
6. retry unconfirmed publication with bounded backoff and jitter.

A crash after JetStream confirmation but before PostgreSQL confirmation may
republish the generation. The deterministic ID provides bounded broker
deduplication; atomic job claims remain the correctness mechanism after that
window.

Publication claims are short dispatcher coordination leases. They must not be
reused as worker execution leases or effect-fencing tokens.

Scanning committed PostgreSQL state is the durability mechanism. A
transactional PostgreSQL `NOTIFY`, process-local signal, or equivalent wake-up
may reduce publication latency, but it is only a hint. The loop must poll with
a bounded interval because wake-ups can be missed, publishers can start after
the commit, and notification delivery cannot be part of the PostgreSQL and
JetStream transaction.

### 10.2 Publisher Deployment Seam

The publisher is a daemon-style runtime and does not require an inbound HTTP
server. V1 runs the Python publication module inside FastAPI processes. A later
deployment may run that same module as a small standalone process when
measurement or operations show a need, including:

- material publication lag or publisher resource contention in API processes;
- excessive polling or PostgreSQL connection use across API replicas;
- a requirement to drain committed publication intent during API deployments
  or API unavailability;
- independent publisher scaling, supervision, credentials, or observability;
  or
- worker-created follow-up publication that should not wait for an API process.

Moving the same module to a standalone deployment does not change the job-row
outbox, message protocol, or correctness model. The deployment must designate
which processes host publishers. Embedded and standalone publishers may
overlap only during a controlled cutover; short PostgreSQL claims and
deterministic message IDs must keep that overlap safe.

The publication path is primarily PostgreSQL and JetStream I/O. A separate
implementation language must not be introduced merely as a presumed
performance optimization. Replacing the Python module requires measured
evidence, an explicit ADR, and compatibility tests proving identical claim,
deduplication, acknowledgement, retry, telemetry, and shutdown semantics.

### 10.3 Message Envelope

Messages must use JSON and contain only:

```json
{
  "version": 1,
  "job_id": "opaque-job-id",
  "publication_generation": 1
}
```

The envelope must not contain job kind, workload policy, function name,
arguments, authoritative input, prompts, assistant output, artifact paths,
access tokens, email addresses, or raw user IDs. Workers derive and validate
that information from PostgreSQL.

Unknown envelope versions, malformed identifiers, mismatched generations, or
unexpected subjects must fail closed and produce bounded operational evidence.

### 10.4 Streams, Subjects, and Consumers

V1 should use one file-backed work-queue stream with allowlisted,
non-overlapping workload subjects. Initial subjects must distinguish at least
latency-sensitive diagnostic work from lower-priority profile inference.

Each workload subject must have one named durable pull consumer shared by all
worker replicas for that workload. Consumers must use explicit
acknowledgement, finite maximum delivery, conservative maximum pending
acknowledgements, and bounded fetch sizes.

New job kinds should reuse a workload subject when latency, resource, privacy,
retry, and scaling characteristics are compatible. A new subject, consumer,
stream, or deployment is justified only by an operational isolation need.
Separate streams may be added for different retention or storage policies.

### 10.5 Self-Hosted Topology and Reconstruction

A single file-backed JetStream node with a persistent local volume is an
acceptable initial self-hosted production topology when the operator accepts
that host-disk loss removes broker state. Replication may be added when broker
availability requirements justify it.

PostgreSQL must be sufficient to reconstruct execution notifications. A
documented recovery operation must recreate streams and consumers and
republish eligible nonterminal jobs without replaying work whose effect policy
forbids it.

JetStream servers must not concurrently share a data directory. PostgreSQL
backup and recovery remain required because broker reconstruction cannot
recover lost application truth.

## 11. Job Runtime, Claims, and Settlement

### 11.1 Handler Registry

The runtime must maintain an allowlisted registry from job kind to input
schemas, supported versions, handler, retry and attempt policy, effect-boundary
policy, and workload class.

Startup must fail closed for duplicate registration, incompatible workload
assignment, or missing policy. An unregistered kind must never invoke arbitrary
code.

### 11.2 Handler Interface

A handler receives a claimed job containing validated input, stable identity,
attempt number, and execution token when applicable. It returns a bounded
outcome equivalent to succeeded, retry after a delay, interrupted, dead, or
cancelled.

Handlers may call application behavior and provider interfaces. They must not
receive a JetStream message, choose a subject, publish a retry, or settle a
delivery.

### 11.3 Delivery Resolution

For every delivery, the runtime must atomically resolve and load the job before
executing domain behavior. Resolution must produce one of these outcomes:

- a terminal job becomes a positively acknowledged no-op;
- an eligible job moves to running, increments its bounded attempt count, and
  receives an execution token when required;
- an early retry delivery is delayed again without executing;
- an already-running job prevents concurrent duplicate execution and is
  settled without removing the only safe recovery path;
- a blocked job consumes no attempt; a stale publication may be acknowledged
  because later release publishes a new generation;
- a mismatched workload class or generation fails closed; or
- a missing, invalid, or inconsistent job is terminated and surfaced.

Exact already-running duplicate settlement must be proven with the pinned NATS
client and consumer configuration. It must not permit concurrent execution or
unsafe post-effect replay.

### 11.4 Execution Tokens and Stale Work

Where delayed or duplicated execution could commit unsafe effects, the runtime
must pass an immutable execution token into every effect-producing path.
Protected writes verify the current token in the same transaction as the write.

Recovery that replaces a running execution must replace the token first.
External systems that cannot participate in fencing require an idempotency key
or durable reservation before invocation.

JetStream `in_progress` extends the transport acknowledgement deadline only.
BikeDoc must not add a generic PostgreSQL heartbeat system by default. A
conservative job execution deadline exists for recovery, not queue reservation.

### 11.5 Settlement Rules

The runtime must settle only after applying the corresponding durable outcome:

- after success, terminal no-op, interrupted, dead, or cancelled commits, send
  positive acknowledgement and await confirmation when supported;
- after a retryable classification and `eligible_at` commit, send delayed NAK;
- while legitimate long-running work continues, send `in_progress` before the
  acknowledgement deadline;
- terminate malformed, unknown, or prohibited messages with a bounded reason
  when supported; and
- on abrupt loss before settlement, allow JetStream redelivery.

Acknowledgement failure after an application commit may redeliver. The next
atomic resolution must turn it into the correct no-op or recovery outcome.

## 12. Retry, Exhaustion, and Recovery Policy

Each job kind owns semantic classification as retryable, interrupted, or
terminal and defines a bounded application attempt policy. Broad retry of
arbitrary exceptions must not be used.

For a retryable failure, PostgreSQL must commit retrying state, attempt count,
bounded error category, and `eligible_at` before delayed NAK. If NAK fails or
the worker exits, acknowledgement expiry may redeliver early; PostgreSQL must
prevent premature execution.

Application attempt count and JetStream delivery count are distinct. Duplicate
delivery of running or terminal work must not consume an application attempt.
JetStream `MaxDeliver` is a transport safety limit, not semantic retry policy.

### 12.1 Diagnostic Turns

The diagnostic effect boundary must be persisted with an execution-token
compare-and-set immediately before execution may invoke ADK, emit assistant
events, call a mutating tool, incur a non-idempotent external effect, or
otherwise make whole-turn replay unsafe.

Pre-boundary failures may retry within policy. Post-boundary failures must not
replay the whole turn. They become `interrupted`, update diagnostic product
state, and append the required public recovery or terminal event exactly once.

### 12.2 Profile Inference

The generic job is the durable execution envelope. The domain-specific
inference run remains the authoritative inference result. Implementation must
define one mapping between application and inference attempts; nested retry
loops must not multiply provider calls beyond policy.

### 12.3 Delivery Exhaustion

Workers should commit an application terminal outcome and acknowledge when the
application attempt limit is reached. JetStream maximum delivery is a final
safety net for crashes and poison deliveries.

Maximum-delivery and termination advisories are operational hints. They must
be observed and correlated when possible but do not replace PostgreSQL job
state. V1 does not require a traditional broker DLQ for valid jobs.

### 12.4 Reconciliation

Reconciliation must cover:

- unconfirmed desired publication generations and abandoned claims;
- suspected duplicate deliveries;
- jobs stranded after worker, broker, or process loss;
- blocked jobs whose dependencies satisfy release policy;
- delivery-exhaustion or termination advisories;
- broker stream or consumer reconstruction; and
- inconsistent application records.

Recovery must use normal eligibility, attempt, and effect-boundary policies. It
must not blindly republish diagnostic work or become a database scheduler.

## 13. Worker Runtime and Resource Ownership

Workers must be native asynchronous processes. Each owns its event loop, NATS
connection, database pool, provider and ADK clients, concurrency semaphore,
and graceful-shutdown lifecycle.

V1 should use separate deployments for latency-sensitive diagnostic and
lower-priority profile-inference workload classes. Each may start as one async
process and scale by adding replicas sharing the durable consumer.

Fetch size, maximum pending acknowledgements, local concurrency, and database
pool size must be configured together. CPU-bound job kinds need an isolated
workload class or explicit process execution rather than blocking the shared
event loop.

Graceful shutdown stops fetching, gives bounded in-flight work time to settle,
and drains NATS resources. Forced termination leaves unacknowledged work for
redelivery and reconciliation.

Provider timeouts, execution deadlines, acknowledgement waits, `in_progress`
cadence, shutdown periods, and concurrency form one timing policy. Worker
supervision belongs to the deployment runtime, not PostgreSQL.

## 14. Cross-Process Prerequisites

### 14.1 Durable ADK Sessions

Diagnostic workers must use PostgreSQL-backed ADK sessions. Process-local
sessions must not be enabled with external workers. The adapter must support
multiple processes and explicit schema migration, versioning, retention, and
resource lifecycle.

### 14.2 Diagnostic Event Wake-Ups

The PostgreSQL event table remains SSE truth. After event commit, API processes
must receive a lossy wake-up and query newer sequence numbers.

V1 should use a Core NATS subject, not a JetStream job subject, because the
wake-up is only a latency hint. A low-frequency PostgreSQL polling fallback is
required. Event commit must never depend on successful wake-up publication.

## 15. Security and Privacy

The durable queue must:

- use TLS outside a trusted single-host production network;
- keep NATS client and monitoring endpoints private;
- use least-privilege publish and subscribe permissions by subject;
- store credentials in deployment secrets and redact NATS URLs;
- accept JSON envelopes only and reject executable serializers;
- allowlist envelope versions, workload subjects, job kinds, input versions,
  and handlers;
- derive authorization, kind, and input from PostgreSQL;
- exclude user content, secrets, raw user IDs, and artifact paths from broker
  payloads and headers;
- retain only bounded, redacted failure details; and
- follow diagnostic telemetry privacy rules.

## 16. Operations and Observability

PostgreSQL jobs are the primary product view. JetStream stream, consumer, and
advisory data are the transport view. NATS administration state must not decide
whether work succeeded or is safe to replay.

BikeDoc must expose inspection of kind, workload class, input version, state,
attempt count, dependency, publication generation, effect boundary, execution
timing, and bounded errors.

Operations must measure:

- pending publication count and oldest age;
- publish acknowledgement latency, duplicates, and failures;
- stored, pending, redelivered, and acknowledgement-pending messages by fixed
  stream and consumer;
- enqueue-to-start latency, execution duration, and outcomes by allowlisted
  kind or workload class;
- active executions, deadline recovery, and stale-token rejections;
- retry, interruption, no-op, dead, and cancellation counts;
- maximum-delivery and termination advisories;
- worker availability and forced termination;
- SSE wake-up latency and polling recovery; and
- PostgreSQL connections across APIs, workers, publishers, and SSE readers.

Identifiers must not be metric labels. Logs and traces may use approved opaque
correlation fields but must not include job payloads or full broker messages.

Alerts must detect publication lag, queue latency, unavailable workers,
publish-acknowledgement failure, JetStream resource alarms, abnormal
redelivery or interruption, maximum-delivery advisories, and database
saturation.

## 17. Verification Requirements

Unit tests must exercise job policy, handler registration, input validation,
outcome mapping, publication, and settlement through fakes without real NATS,
PostgreSQL, providers, or models.

Integration tests must use real PostgreSQL and JetStream with fake model and
provider behavior. Before rollout, tests must prove:

1. atomic creation and rollback of product state, jobs, inputs, dependencies,
   and publication generation;
2. HTTP idempotency cannot create duplicate logical jobs;
3. acknowledged publication survives the configured broker restart;
4. a publisher crash in the acknowledgement window cannot duplicate effects;
5. deterministic job-generation message IDs provide broker deduplication;
6. completed jobs handle duplicate delivery as a no-op;
7. atomic claim-and-load prevents concurrent duplicate execution;
8. replaced execution tokens prevent stale protected writes;
9. early redelivery cannot bypass `eligible_at`;
10. pre-effect crashes recover within policy;
11. post-effect diagnostic crashes become interrupted without another ADK run;
12. delayed NAK implements only application-classified retries;
13. duplicate delivery does not consume an application attempt;
14. dependent work releases and publishes once;
15. profile backlog cannot consume diagnostic capacity;
16. unknown envelope, kind, input version, subject, and generation fail closed;
17. long work sends progress acknowledgement in time;
18. acknowledgement failure after commit redelivers safely;
19. maximum-delivery advisories reach reconciliation;
20. broker reconstruction cannot replay terminal or unsafe post-effect work;
21. worker events wake another API process and polling recovers missed hints;
22. graceful and forced shutdown produce safe outcomes;
23. the same publication module passes embedded and standalone lifecycle
    tests; and
24. messages, headers, logs, and metrics contain no prohibited content.

Compatibility tests must validate pinned NATS server and Python client,
PostgreSQL driver, async resources, and ADK sessions. Load tests must establish
safe concurrency, connection budgets, acknowledgement timing, fetch sizes,
broker recovery, and event latency.

## 18. Rollout and Cutover

Implementation should proceed in reviewable stages:

1. prove publish acknowledgement, pull consumption, delayed NAK, progress and
   confirmed acknowledgement, and graceful shutdown with the pinned client;
2. prove durable ADK sessions and cross-process event wake-ups;
3. add generic jobs, versioned inputs, publication generations, handler
   registry, policy module, and test adapters;
4. add the standalone-capable publication entry point, host the publication
   module in FastAPI, and deploy reconciliation, initial consumers, and async
   workers;
5. canary profile inference;
6. add diagnostic effect fencing and canary diagnostic execution; and
7. remove production `BackgroundTasks` after verification passes.

Exactly one executor must be selected for each logical job. In-process and
JetStream paths must never execute the same job concurrently.

Rollback must stop or drain consumers, inspect running jobs and effect
boundaries, preserve PostgreSQL job state, and enable fallback only for work
proven safe to run.

## 19. Deferred Implementation Decisions

The following remain for implementation planning or later ADRs:

- pinned NATS server and Python client versions;
- concrete stream, subject, durable consumer, and account names;
- exact database columns, constraints, indexes, and retention;
- JSON-versus-domain-reference input choices by job kind;
- concrete package and command names;
- publication batch size, interval, claim duration, and replica count;
- whether and when measurements justify activating the standalone publisher
  deployment;
- retry limits and delay curves by job kind;
- acknowledgement waits, progress cadence, deadlines, concurrency, and
  autoscaling;
- profile-inference dependency release states;
- the self-hosted backup and broker-reconstruction runbook;
- when availability justifies JetStream replication;
- operator commands and authorization;
- when fan-out or integration events justify a separate outbox; and
- deployment-specific secrets and monitoring.

These choices must not weaken the invariants or change the ownership split
without updating this spec.

## 20. Completion Criteria

The design is implemented when:

- every accepted operation has durable PostgreSQL job state and versioned
  input;
- acceptance and publication intent are atomic;
- the publication implementation is shared by its FastAPI and standalone
  lifecycle hosts;
- API publication-loop, broker, and worker restarts cannot silently lose work;
- NATS downtime accumulates recoverable unpublished generations;
- messages contain only the minimal delivery envelope;
- workers atomically claim and load authoritative PostgreSQL input;
- new kinds require only input, handler, policy, and workload assignment unless
  real isolation is needed;
- duplicate delivery cannot duplicate committed product effects;
- diagnostic pre-effect failures may retry while post-effect failures cannot
  replay the whole turn;
- stale execution is rejected from protected writes;
- normal retry uses application classification and delayed NAK;
- PostgreSQL dead and interrupted state outranks broker advisories;
- broker reconstruction is tested;
- ADK state and SSE delivery work across processes;
- operationally different workloads are isolated;
- inspection, reconciliation, alerts, and failure-injection tests exist;
- messages, headers, logs, and metrics contain no prohibited content; and
- production job execution no longer uses FastAPI `BackgroundTasks`.
