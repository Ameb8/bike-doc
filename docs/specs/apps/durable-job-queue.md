# BikeDoc Durable Background Job Queue Spec

Status: Canonical v1.10
Last updated: 2026-08-25

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
- atomic, independent acceptance of diagnostic and profile-inference work;
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
Generations are monotonic wake-up epochs, not an event history: unpublished
intermediate generations may coalesce to the latest desired generation.

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

### Worker Process Role

A **worker process role** is an independently deployable host of the shared job
runtime for exactly one workload class. A role supplies that workload class's
complete allowlisted handler registry and process-specific resource
composition. Multiple roles may use thin, distinct program entry points or one
parameterized entry point, but they must reuse the same runtime implementation
rather than copy its consumption, claim, settlement, retry, telemetry, or
shutdown logic.

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

**Reconciliation** is the bounded automatic safety net that recovers expired
executions, republishes eligible nonterminal jobs that show no execution
progress beyond a conservative threshold, and idempotently terminalizes
interrupted diagnostic turns. It is not a parallel queue, a normal retry
scheduler, a broker-advisory processor, or a generic application-data repair
system.

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
| Worker programs | Workload-specific process roles over one shared Python runtime |
| Job dispatch | Workload subject selects a worker pool; PostgreSQL job kind and input version select an allowlisted handler |
| Delivery guarantee | At least once |
| Broker payload | Version, opaque job ID, and publication generation only |
| Routing | Workload class, not job kind |
| Consumption | Durable pull consumers with explicit acknowledgement |
| Retry | Application classifies; JetStream delayed NAK performs normal redelivery |
| Duplicate handling | Atomic PostgreSQL claim and optional execution token |
| Dead work | PostgreSQL terminal state; broker advisories are hints |
| Diagnostic handled post-effect failure | Persist the required product error and completion outcome, then settle the job as succeeded |
| Diagnostic abrupt post-effect loss | Reconcile as interrupted; never replay automatically |
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
5. PostgreSQL determines job kind, workload class, input, eligibility, and
   product outcome.
6. Every message may be delivered more than once.
7. Delivery of a completed or terminal job must become a safely settled no-op.
8. An atomic PostgreSQL transition must prevent concurrent duplicate
   execution.
9. A replaced execution token must reject later protected writes by stale work.
   A job's execution deadline is a hard, non-renewable validity boundary for
   that token, even if the originating worker remains alive.
10. A diagnostic turn must not replay automatically after its effect boundary.
    A handled runner error that durably terminalizes the turn is a succeeded job
    outcome; `interrupted` is reserved for post-boundary execution loss that did
    not finish normal product terminalization.
11. Positive acknowledgement must follow the durable outcome that makes
    removal of the delivery safe.
12. A crash after broker confirmation but before PostgreSQL confirmation may
    duplicate publication and must remain safe.
13. Broker loss must be recoverable by republishing eligible nonterminal jobs
    from PostgreSQL.
14. Confirming an older publication generation must not confirm or erase newer
    publication intent recorded while that publish was in flight.
15. A job row must not be deleted while JetStream can still deliver a message
    for it or while publication or recovery remains incomplete.
16. Messages, metrics, and routine logs must not contain user-authored content
    or secrets.
17. Workloads with materially different operational characteristics must be
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
generation, execution eligibility, effect-boundary policy, outcome transitions,
and reconciliation.

FastAPI application processes host the V1 maintenance control plane containing
publication and reconciliation loops. It is reusable asynchronous Python
module code, not route logic. Publication claims jobs with unconfirmed desired
generations, publishes through the JetStream adapter, waits for a publish
acknowledgement, and records confirmation. Reconciliation scans and claims
bounded repair work across all workload classes. Multiple API replicas may run
both loops safely. V1 does not require a separate dispatcher or reconciler
deployment.

V1 does not implement a standalone maintenance process entry point. The
maintenance module must instead expose a small lifecycle surface and keep
FastAPI hosting concerns outside its publication and reconciliation logic. This
preserves a clean extraction seam if measurements later justify independent
maintenance scaling or availability without building and testing an unused
deployment mode now.

JetStream owns stream storage, durable consumer state, acknowledgement
deadlines, redelivery, and delivery metadata. It does not decide product state
or whether a job may run.

The job runtime owns pull consumption, bounded concurrency, envelope
validation, atomic claim-and-load, handler selection, progress acknowledgements,
outcome persistence, and final settlement.

BikeDoc must implement this runtime once as shared Python code. Independently
deployed worker process roles host it with workload-specific configuration,
resources, and handler registries. A role may have its own thin executable or
module entry point, or several roles may invoke one parameterized entry point.
That packaging choice must not duplicate runtime behavior or prevent separate
deployment, credentials, scaling, concurrency, or supervision by workload
class.

Job handlers own job-kind-specific behavior and return a bounded outcome such
as succeeded, retry after a delay, interrupted, or dead. They must not know how
JetStream settlement works.

Reconciliation recovers expired running jobs, republishes eligible nonterminal
jobs that have made no execution progress beyond a conservative threshold, and
idempotently terminalizes interrupted diagnostic turns. It runs in the
API-hosted V1 maintenance control plane, not in workload workers, and covers all
workload classes. When recovery requires another notification, it advances
durable publication intent; the publication loop remains the only JetStream job
publisher.

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

The maintenance module's interfaces must own publication batching, claims,
backoff, acknowledgement and confirmation, bounded reconciliation scans and
claims, and graceful shutdown behind a small lifecycle surface. FastAPI startup
hosts those interfaces in V1. A future standalone command must host the same
interfaces rather than duplicate their implementation.

Material implementation changes to module responsibilities and dependency
directions must also update `apps/api/ARCHITECTURE.md`.

## 8. Durable Acceptance

### 8.1 Turn Acceptance

For a newly accepted diagnostic turn, one PostgreSQL transaction must:

1. validate the request and current product state;
2. persist the accepted turn and its initial public event;
3. create or reuse the app-owned diagnostic phase-session reference without
   calling the ADK session adapter; a newly created reference has a null
   `adk_session_id` until worker initialization;
4. update the repair session as required by diagnostic specs;
5. create one executable `diagnostic_turn` job with versioned input;
6. set its desired publication generation ahead of its confirmed generation;
7. when the turn contains eligible image evidence, create one independently
   executable `profile_inference` job with versioned input; and
8. set that job's desired publication generation ahead of its confirmed
   generation.

The transaction either commits all required records or none. HTTP idempotency
must return an existing acceptance without creating another logical job, with
a database uniqueness constraint providing the race-safe guarantee. Acceptance
must not call ADK or depend on ADK session-store availability.

### 8.2 Producer Rule

The FastAPI backend is the sole V1 producer of application jobs, and its
processes host the sole V1 publisher module. Product code must record jobs
through the background-job interface rather than construct JetStream messages
directly. A long-lived publication coroutine in the FastAPI deployment is the
reliable publication path.

An opportunistic post-commit publish may reduce latency, but it must use the
same publication adapter and must not replace the durable publication loop.
Failure of that fast path must leave the committed job publishable.

### 8.3 Independent Turn Work

Profile inference triggered by a turn must be represented at original
acceptance time and must not depend on the diagnostic job. Both jobs are
independently executable and both publication intents commit atomically with
the accepted turn.

Diagnostic failure, retry, interruption, or delayed delivery must not prevent
profile inference from running. Their separate workload classes provide
capacity and failure isolation; neither job's acknowledgement or product
outcome controls the other's eligibility.

## 9. Durable Job Record and Inputs

This section defines logical requirements, not final SQL DDL.

### 9.1 Generic Job Fields

Each job must retain enough information to determine:

- stable job identity and immutable logical-job deduplication key;
- registered job kind and workload class;
- input schema version;
- small immutable JSON input and/or stable domain references;
- current product-relevant state and earliest eligible time;
- bounded application attempt count and attempt limit;
- desired and last confirmed publication generations;
- publication eligibility, short dispatcher claim, confirmation time, and
  bounded transport error;
- execution start, conservative deadline, and execution token when required;
- whether and when its effect boundary was crossed;
- first start and terminal completion times; and
- latest bounded, redacted error category.

The lifecycle must distinguish queued, running, retrying, succeeded,
interrupted, and dead work. V1 has no business-level job cancellation state or
handler outcome. A future user, operator, or domain cancellation feature must
define its authorization, fencing, effect-boundary, and product-state rules
before adding cancellation to this lifecycle. Process shutdown and runtime
task cancellation are execution-loss mechanics, not job cancellation.

`dead` is terminal in V1 and must not transition back to a nonterminal state.
Reconciliation must not revive it. If later recovery requires execution, it
must create a replacement job through job-kind-specific behavior defined before
that recovery is used; the original dead job remains unchanged. A diagnostic
job whose accepted public turn has been terminalized is retried only through a
newly accepted user turn and new job, preserving the public event history and
diagnostic no-replay rule.

### 9.2 Versioned Job Inputs

Each job kind must register a strict, versioned input schema. Workers validate
stored input before invoking a handler. Unknown kinds or input versions fail
closed and become visible to operational inspection.

Inputs should use opaque identifiers when authoritative domain records already
exist. They should include an expected revision or immutable snapshot when a
later domain change must not alter the accepted instruction.

For `diagnostic_turn`, the immutable accepted instruction consists of the
accepted turn content and its submitted artifact references. Mutable supporting
context—including the bike profile, resolved claims, and repair history—is not
snapshotted into the job and is read from current authoritative state when the
handler or a diagnostic tool needs it.

The strict `diagnostic_turn.v1` job input is:

```json
{
  "turn_id": "turn_..."
}
```

It permits no additional fields. The immutable turn row is authoritative for
the accepted message, submitted artifact references, repair-session identity,
and phase-session identity. The job row separately supplies the job kind,
workload class, and input schema version, so the input must not duplicate them.

The strict `profile_inference.v1` job input is:

```json
{
  "turn_id": "turn_...",
  "inference_schema_version": "bike_profile_inference.v1",
  "extractor_version": "..."
}
```

It permits no additional fields. Both behavior versions are pinned when the job
is accepted. The handler resolves immutable artifact references and related
domain identities through the accepted turn, then idempotently creates or
reuses the authoritative inference run identified by the existing unique tuple
of turn ID, inference schema version, and extractor version. The job input must
not duplicate artifact, bike, or repair-session IDs.

Every job has an immutable `deduplication_key`, with a database uniqueness
constraint over `(job_kind, deduplication_key)`. This key is internal logical-job
identity and is distinct from the client's HTTP idempotency key. The initial
job identities are:

- `diagnostic_turn`: the accepted turn ID; and
- `profile_inference`: the canonical tuple of accepted turn ID, inference schema
  version, and extractor version.

The background-job interface must derive or validate a stable, unambiguous
encoding of that identity rather than inspect JSON during conflict handling.
Concurrent attempts to record the same logical job must insert or resolve the
same row through the database constraint.

Because diagnostic and profile-inference jobs execute independently, facts
inferred from the same turn may become visible to that diagnostic execution if
profile inference commits first. This timing is intentionally not deterministic
and does not invalidate the accepted instruction. Existing safety policy must
continue to prevent image-inferred profile facts alone from authorizing risky
guidance.

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

Publication fields on the job row are canonical while each job has one
destination and needs only its latest desired execution notification published.
They do not preserve an independently publishable history of every intermediate
generation.

A separate outbox requires an explicit design update and is appropriate when
one job generation must produce multiple independent messages, fan out to
several destinations, publish non-job integration events, or preserve
publication history independently of job retention. One acceptance transaction
may create multiple jobs without requiring a separate outbox because each job
row owns only its own publication generation and destination.

### 9.6 Job Retention and Deletion

Nonterminal jobs and jobs whose confirmed publication generation trails their
desired generation must never be deleted. A terminal job may be deleted only
after a retention period long enough that JetStream can no longer deliver any
message for it and no application recovery or required operational audit can
still refer to it.

V1 retains the complete job row for that period rather than introducing a
separate tombstone mechanism or requiring cleanup to inspect live broker state.
The concrete retention duration is deployment configuration, but it must
exceed the configured JetStream message lifetime and every longer retry,
recovery, or audit horizon.

## 10. Publication and JetStream Contract

### 10.1 Publication Loop

PostgreSQL and JetStream do not share a transaction. The publication loop must:

1. claim due jobs whose desired generation exceeds their confirmed generation;
2. bind each claim to the exact latest desired generation observed by that
   claim;
3. derive an allowlisted subject from the stored workload class;
4. publish a minimal JSON envelope with a deterministic `Nats-Msg-Id` based on
   job ID and the claimed generation;
5. require a JetStream publish acknowledgement;
6. confirm only the claimed generation in PostgreSQL; and
7. retry unconfirmed publication with bounded backoff and jitter.

An acknowledgement for generation `g` may advance the confirmed generation to
at most `g`. If the desired generation advanced beyond `g` while publication
was in flight, that newer intent must remain publishable. Unpublished
intermediate generations may coalesce: a new claim may publish only the latest
desired generation because generations are wake-up epochs rather than an
ordered event history.

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

### 10.2 Maintenance Deployment Seam

The maintenance control plane is daemon-style module code even though V1 hosts
it inside FastAPI processes. V1 does not provide or test a standalone process
entry point. A later deployment may add a thin standalone host around those
same modules when measurement or operations show a need, including:

- material publication lag or publisher resource contention in API processes;
- excessive polling or PostgreSQL connection use across API replicas;
- a requirement to drain committed publication intent during API deployments
  or API unavailability;
- independent maintenance scaling, supervision, credentials, or observability;
  or
- worker-created follow-up publication that should not wait for an API process.

Moving the same modules to a standalone deployment does not change the job-row
outbox, message protocol, recovery policy, or correctness model. The deployment
must designate which processes host maintenance. Embedded and standalone hosts
may overlap only during a controlled cutover; short PostgreSQL claims,
conditional recovery transitions, and deterministic message IDs must keep that
overlap safe.

The publication path is primarily PostgreSQL and JetStream I/O. A separate
implementation language must not be introduced merely as a presumed
performance optimization. Replacing the Python modules requires measured
evidence, an explicit ADR, and compatibility tests proving identical claim,
deduplication, acknowledgement, retry, reconciliation, telemetry, and shutdown
semantics.

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

Unknown envelope versions, malformed identifiers, impossible future
generations, or unexpected subjects must fail closed and produce bounded
operational evidence. A lower generation than the job's current desired
generation is an expected stale notification under generation coalescing and
follows the stale no-op rule in Section 11.3.

### 10.4 Streams, Subjects, and Consumers

V1 should use one file-backed work-queue stream with allowlisted,
non-overlapping workload subjects. Initial subjects must distinguish at least
latency-sensitive diagnostic work from lower-priority profile inference.

Each workload subject must have one named durable pull consumer shared by all
worker replicas for that workload. Consumers must use explicit
acknowledgement, finite maximum delivery, conservative maximum pending
acknowledgements, and bounded fetch sizes.

Subject routing is coarse-grained by workload class; it does not select a job
handler. After receipt, the worker atomically claims and loads the authoritative
PostgreSQL job row, verifies that its stored workload class and publication
generation match the delivery, then dispatches by the stored job kind and input
version through its allowlisted registry. The broker envelope must remain
independent of Python function or handler names.

Every replica sharing a workload consumer must register the complete supported
handler set for all job kinds that producers may route to that workload. A
deployment must not mix kind-specific replicas with incomplete registries on
one durable consumer, because any replica may receive any delivery for that
workload. If a job truly requires a separately deployable program, it also
requires an operationally justified workload-class routing boundary rather
than relying on consumer-side filtering.

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
policy, hard maximum execution duration, timeout grace, and workload class.

Startup must fail closed for duplicate registration, incompatible workload
assignment, or missing policy. An unregistered kind must never invoke arbitrary
code.

Dispatch must follow this sequence:

1. the workload subject selects the eligible worker process role;
2. the delivery envelope identifies only a job and publication generation;
3. atomic PostgreSQL resolution returns the authoritative kind, workload class,
   input version, input, attempt, and execution token when applicable;
4. the runtime verifies the workload class and generation;
5. the registry selects the exact definition by job kind and input version;
6. the registered input schema validates the stored input; and
7. only then may the registered handler execute.

Database values must not be interpreted as import paths, module names,
function names, shell commands, or another form of dynamic code selection.
For a trusted existing job, an unknown kind, unsupported version, or
schema-invalid stored input is a permanent definition/input failure. Before
terminating the delivery, the runtime must atomically mark the job `dead` with a
bounded error category. No application attempt is consumed because no handler
started.

Kind and input-version rollout must be expand-first. Every worker replica for a
workload must support every kind and version that its producers may create or
that may still exist in a nonterminal job. Worker support for a new kind or
version must be fully deployed before producers may create it. During a
migration, workers retain both old and new definitions.

Support for an old kind or version may be removed only after PostgreSQL has no
nonterminal jobs using it and the retention invariant guarantees JetStream can
no longer redeliver one of its messages. Encountering an unknown kind or
version remains a fail-closed deployment or configuration error; it must not be
classified as an ordinary retryable job failure. After compatibility is
restored, any recovery that requires execution must create a replacement job;
the dead job is not revived.

### 11.2 Handler Interface

A handler receives a claimed job containing validated input, stable identity,
attempt number, and execution token when applicable. It returns a bounded
outcome equivalent to succeeded, retry after a delay, interrupted, or dead.

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
- an already-running job with an unexpired execution deadline prevents
  concurrent duplicate execution, consumes no application attempt, and sends a
  delayed NAK for the remaining time until that deadline;
- a delivery generation lower than the current desired generation is a
  positively acknowledged stale no-op and consumes no application attempt;
- a delivery generation higher than the current desired generation, or a
  mismatched workload class or subject, is terminated and surfaced without
  changing job state; or
- a trusted existing job with a permanent definition/input failure becomes
  `dead` before the delivery is terminated; or
- a malformed envelope, missing job, or identity that cannot be trusted is
  terminated and surfaced without a job-state mutation.

A generation equal to the current desired generation proceeds through normal
eligibility resolution. A stale lower generation must not clear, confirm, or
otherwise alter the newer publication intent.

The delayed NAK closes only the duplicate delivery while retaining the message
for recovery after the authoritative PostgreSQL deadline. It requires no
worker-to-worker coordination or additional heartbeat. If the original
execution commits a terminal outcome first, its acknowledgement or the later
terminal no-op settles the message. The pinned NATS client and consumer
configuration must prove this behavior and ensure duplicate handling cannot
exhaust maximum delivery before deadline recovery.

### 11.4 Execution Tokens and Stale Work

Where delayed or duplicated execution could commit unsafe effects, the runtime
must pass an immutable execution token into every effect-producing path.
Protected writes verify the current token in the same transaction as the write.

Each claim records a hard execution deadline derived from the registered job
kind's maximum execution duration and bounded timeout grace. The runtime must
enforce a handler timeout early enough to cancel the runtime task and finish the
grace period no later than that deadline. This runtime cancellation does not
create a `cancelled` job outcome. The deadline must not be renewed. After it
passes, the execution token is no longer valid even if the originating worker
remains alive.

Recovery that replaces a running execution after its deadline must replace the
token first. A late execution must fail all subsequent protected writes.
External systems that cannot participate in fencing require an idempotency key
or durable reservation before invocation, and the runtime must not assume that
task cancellation alone stops an in-flight external operation.

JetStream `in_progress` extends the transport acknowledgement deadline only.
It never extends the PostgreSQL execution deadline. BikeDoc must not add a
generic PostgreSQL heartbeat system by default.

### 11.5 Settlement Rules

The runtime must settle only after applying the corresponding durable outcome:

- after success, terminal no-op, interrupted, or dead commits, send
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

Before crossing that boundary, the handler must idempotently ensure that the
app-owned diagnostic phase session has its durable ADK session. A null
`adk_session_id` means the phase session has not yet been initialized. The
handler derives a deterministic ADK identity from the app-owned phase-session
ID, ensures that session exists, and atomically stores the identity only while
the field remains null. A crash after ADK creation but before binding is safe
because the next attempt derives and ensures the same identity.

Once `adk_session_id` is non-null, the handler must resume that exact durable
session. A confirmed missing session after binding represents lost state and
must not be silently recreated or replaced. Failure to initialize or resume the
session before the effect boundary follows the diagnostic pre-boundary failure
policy.

Pre-boundary failures may retry within policy. After the boundary, a handled
runner error must persist the diagnostic contract's required `error` and
`turn.completed` events, restore the required product state, and return a
succeeded job outcome. In this context, job success means that processing
reached a durable, safely settled product outcome; it does not mean the model
produced a successful answer.

If pre-boundary retries are exhausted or another permanent pre-boundary failure
prevents diagnostic execution, the same transaction that marks the job `dead`
must restore the repair session to `awaiting_user`, append exactly one `error`
event with `code: diagnostic_processing_error` and `retryable: true`, and append
exactly one `turn.completed` event. This terminalization is app-owned and
idempotent even when failure occurs before handler dispatch. It prevents an
accepted turn from leaving its repair session permanently `running`.

An abrupt or uncaught post-boundary execution loss that did not complete normal
product terminalization becomes `interrupted`. Reconciliation must atomically
restore the repair session to `awaiting_user`, append an `error` event with
`code: diagnostic_processing_error` and `retryable: true`, append the required
`turn.completed` event immediately afterward, and terminalize the job without
rerunning ADK. The recovery transaction must be idempotent so repeated
reconciliation cannot append either event twice or produce an inconsistent
session snapshot.

The eventual delivery for that interrupted job becomes a positively
acknowledged terminal no-op. The public `retryable: true` value means the user
may initiate the diagnostic contract's manual retry flow; it must not cause
automatic replay of the same job.

### 12.2 Profile Inference

The generic job is the durable execution envelope. The domain-specific
inference run remains the authoritative inference result. Profile inference has
no effect boundary that prohibits whole-job replay: its BikeDoc writes must be
idempotent for the versioned inference-run identity and must not create
duplicate claims or repeat profile mutations.

An ambiguous crash may therefore repeat the structured model call and incur
another provider charge. V1 accepts that tradeoff rather than adding a durable
provider-result checkpoint or claiming exactly-once provider invocation. The
job attempt policy is the single provider-call budget: provider adapters and
the domain-specific inference module must not add nested retries that can
multiply calls beyond that bounded budget.

### 12.3 Delivery Exhaustion

Workers should commit an application terminal outcome and acknowledge when the
application attempt limit is reached. JetStream maximum delivery is a final
safety net for crashes and poison deliveries.

Maximum-delivery and termination advisories are operational hints. They must
be observed and correlated when possible but do not replace PostgreSQL job
state. V1 does not require a traditional broker DLQ for valid jobs.

### 12.4 Reconciliation

Automatic V1 reconciliation is limited to:

1. recovering a `running` job after its hard execution deadline according to
   its attempt and effect-boundary policy;
2. advancing publication intent for an eligible nonterminal job that has shown
   no execution progress beyond a conservative configured threshold; and
3. idempotently terminalizing a diagnostic turn whose post-boundary execution
   was lost, including its required public events and product-state repair.

The no-progress repair is notification recovery, not an application attempt or
semantic retry. It must not republish a diagnostic job after its effect boundary
or bypass `eligible_at`, attempt limits, or another active execution.

The publication loop recovers unconfirmed generations and abandoned publication
claims. Atomic delivery resolution handles duplicates. Maximum-delivery and
termination advisories feed metrics and alerts only and must not directly mutate
job or product state. Stream and consumer reconstruction is an explicit
operator-run recovery procedure. V1 reconciliation must not perform generic
repair of unspecified inconsistent application records.

## 13. Worker Runtime and Resource Ownership

Workers must be native asynchronous processes. Each owns its event loop, NATS
connection, database pool, provider and ADK clients, concurrency semaphore,
and graceful-shutdown lifecycle.

V1 should use separate deployments for latency-sensitive diagnostic and
lower-priority profile-inference workload classes. Each may start as one async
process and scale by adding replicas sharing the durable consumer.

These deployments are separate running process roles, but they should not be
separate implementations of queue behavior. Both must import the shared job
runtime and provide only their workload-specific composition: handler
definitions, provider and ADK resources, credentials, concurrency, and timing
configuration. Thin workload-specific entry points are preferred when they
reduce configuration mistakes; one parameterized entry point is also
conforming. Separate packages, container images, or implementation languages
require an operational reason and must preserve the same runtime contracts.

A growing number of job kinds must not imply the same growth in worker process
roles. New kinds should join an existing role when their operational
characteristics are compatible. For example, multiple latency-sensitive ADK
turn kinds may share one interactive-agent workload and registry, while
lower-priority ADK enrichment kinds may share another. Create a new role only
when latency, resources, security, privacy, retry behavior, scaling, or failure
isolation justify a new workload class.

Fetch size, maximum pending acknowledgements, local concurrency, and database
pool size must be configured together. CPU-bound job kinds need an isolated
workload class or explicit process execution rather than blocking the shared
event loop.

Graceful shutdown stops fetching, gives bounded in-flight work time to settle,
and drains NATS resources. Forced termination leaves unacknowledged work for
redelivery and, after the authoritative execution deadline, bounded
reconciliation.

Provider timeouts, execution deadlines, acknowledgement waits, `in_progress`
cadence, shutdown periods, and concurrency form one timing policy. Worker
supervision belongs to the deployment runtime, not PostgreSQL.

For every job kind, provider timeouts and the handler timeout must fit inside
the hard maximum execution duration, with enough remaining timeout grace to
reach a durable outcome or leave the execution for deadline recovery.

## 14. Cross-Process Prerequisites

### 14.1 Durable ADK Sessions

Diagnostic workers must use PostgreSQL-backed ADK sessions. Process-local
sessions must not be enabled with external workers. The adapter must support
multiple processes and explicit schema migration, versioning, retention, and
resource lifecycle.

The app-owned `repair_phase_sessions.adk_session_id` must be nullable. Null is
the sole V1 representation of an ADK session that has not yet been initialized;
non-null means the phase session has been bound to that exact durable ADK
identity. Uniqueness applies to non-null IDs.

The ADK session adapter owns its own persistence lifecycle and does not
participate in the turn-acceptance transaction. The diagnostic worker invokes
its idempotent ensure-or-resume operation as a pre-effect execution
precondition. This avoids a cross-module transaction while keeping committed
turn acceptance independent of ADK availability.

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
attempt count, publication generation, effect boundary, execution timing, and
bounded errors.

Operations must measure:

- pending publication count and oldest age;
- publish acknowledgement latency, duplicates, and failures;
- stored, pending, redelivered, and acknowledgement-pending messages by fixed
  stream and consumer;
- enqueue-to-start latency, execution duration, and outcomes by allowlisted
  kind or workload class;
- active executions, deadline recovery, and stale-token rejections;
- retry, interruption, no-op, and dead counts;
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

1. atomic creation and rollback of product state, jobs, inputs, and publication
   generations, including both independently executable jobs for an eligible
   image turn, without calling the ADK session adapter;
2. HTTP idempotency and concurrent job recording cannot create duplicate
   logical jobs, with `(job_kind, deduplication_key)` enforcing the race-safe
   identity independently of the client key;
3. acknowledged publication survives the configured broker restart;
4. a publisher crash in the acknowledgement window cannot duplicate effects;
5. deterministic job-generation message IDs provide broker deduplication;
6. an older in-flight publication confirmation cannot confirm a newer desired
   generation, while unpublished intermediate generations coalesce safely;
7. completed jobs handle duplicate delivery as a no-op;
8. atomic claim-and-load prevents concurrent duplicate execution;
9. replaced execution tokens prevent stale protected writes;
10. a handler timeout and timeout grace finish by the hard execution
   deadline, progress acknowledgements do not extend it, and late protected
   writes are rejected;
11. early redelivery cannot bypass `eligible_at`;
12. pre-effect crashes recover within policy;
13. post-effect diagnostic crashes atomically become interrupted, restore the
    session to `awaiting_user`, and append exactly one
    `diagnostic_processing_error` and `turn.completed` pair without another ADK
    run;
14. a phase session is accepted with a null ADK ID, deterministic ADK session
    ensure-and-bind is idempotent across a crash between creation and binding,
    occurs before the effect boundary, and never recreates or replaces a
    confirmed missing session after binding;
15. an ambiguous profile-inference crash may repeat the model call but cannot
    duplicate claims or profile mutations, and all calls share one attempt
    budget;
16. delayed NAK implements only application-classified retries;
17. duplicate delivery does not consume an application attempt;
18. an unexpired running duplicate receives a delayed NAK until the execution
    deadline and remains available for deadline recovery;
19. diagnostic and profile-inference work publish independently and neither
    outcome controls the other's eligibility;
20. diagnostic execution preserves immutable accepted turn content and
    artifact references while reading mutable supporting context from current
    authoritative state, including same-turn inferred profile facts when
    already committed;
21. `diagnostic_turn.v1` accepts only a `turn_id` and resolves all accepted
    instruction data through the immutable authoritative turn;
22. `profile_inference.v1` pins the turn, inference schema, and extractor
    versions, rejects additional fields, and idempotently resolves one
    authoritative inference run;
23. profile backlog cannot consume diagnostic capacity;
24. lower stale generations become attempt-free acknowledged no-ops while
    impossible future generations and subject mismatches terminate without
    changing job state;
25. malformed or missing identities terminate without mutation, while a trusted
    job with an unknown kind, unsupported version, or invalid stored input
    becomes `dead` without consuming an application attempt;
26. a `dead` job cannot return to a nonterminal state, and any later
    job-kind-specific recovery creates a replacement without editing the
    original;
27. pre-boundary diagnostic exhaustion atomically marks the job `dead`, restores
    the session to `awaiting_user`, and appends exactly one
    `diagnostic_processing_error` and `turn.completed` pair;
28. long work sends progress acknowledgement in time;
29. acknowledgement failure after commit redelivers safely;
30. an eligible nonterminal job with no execution progress beyond the configured
    threshold advances publication intent without consuming an application
    attempt, bypassing `eligible_at`, or replaying unsafe post-effect work;
31. maximum-delivery and termination advisories reach metrics and alerts without
    directly mutating job or product state;
32. the explicit broker-reconstruction procedure cannot replay terminal or
    unsafe post-effect work;
33. worker events wake another API process and polling recovers missed hints;
34. graceful and forced shutdown produce safe outcomes;
35. publication and reconciliation modules pass their FastAPI-hosted lifecycle
    tests without depending on route logic;
36. cleanup cannot delete a nonterminal or publication-pending job, and a
    terminal job remains resolvable for longer than JetStream can deliver it;
37. a rolling kind or input-version migration cannot route new work to an
    incompatible worker, and old support is retained through its final possible
    redelivery; and
38. messages, headers, logs, and metrics contain no prohibited content.

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
4. host the reusable publication and reconciliation modules in FastAPI, and
   deploy initial consumers and async workers;
5. canary profile inference;
6. add diagnostic effect fencing and canary diagnostic execution; and
7. remove production `BackgroundTasks` after verification passes.

For every later job-kind or input-version change, rollout must deploy compatible
workers before enabling producers. Old and new definitions overlap until no
nonterminal old-version job remains and no retained broker delivery can refer
to one.

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
- JSON-versus-domain-reference input choices for future job kinds;
- concrete package and command names;
- publication batch size, interval, claim duration, and replica count;
- whether and when measurements justify implementing and deploying a
  standalone maintenance host;
- retry limits and delay curves by job kind;
- acknowledgement waits, progress cadence, deadlines, concurrency, and
  autoscaling;
- the self-hosted backup and broker-reconstruction runbook;
- when availability justifies JetStream replication;
- when fan-out or integration events justify a separate outbox; and
- deployment-specific secrets and monitoring.

These choices must not weaken the invariants or change the ownership split
without updating this spec.

## 20. Completion Criteria

The design is implemented when:

- every accepted operation has durable PostgreSQL job state and versioned
  input;
- acceptance and publication intent are atomic;
- publication and reconciliation are reusable modules hosted by FastAPI rather
  than route logic or an unnecessary V1 standalone process;
- API publication-loop, broker, and worker restarts cannot silently lose work;
- NATS downtime accumulates recoverable unpublished generations;
- messages contain only the minimal delivery envelope;
- workers atomically claim and load authoritative PostgreSQL input;
- workload subjects route to process roles and the shared registry dispatches
  authoritative PostgreSQL job kinds and input versions;
- workload-specific worker entry points reuse one job-runtime implementation
  without duplicating consumption, claim, retry, settlement, or lifecycle code;
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
