# BikeDoc Durable Background Job Queue Spec

Status: Canonical v1.0
Last updated: 2026-08-21

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

- PostgreSQL as the authoritative job ledger;
- a PostgreSQL transactional outbox for atomic job acceptance;
- RabbitMQ quorum queues as the durable message transport;
- Celery as the worker and task-consumption framework;
- independently scalable worker pools for distinct workloads;
- durable Google ADK session storage; and
- cross-process wake-ups for persisted SSE events.

This design provides **at-least-once delivery**, not exactly-once execution.
Correctness comes from durable application state, idempotent claims, lease
fencing, explicit effect boundaries, and conservative retry rules. RabbitMQ
and Celery alone do not provide those properties.

## 2. Scope

This spec covers:

- durable creation and publication of background jobs;
- the job, attempt, and outbox records required for recovery;
- RabbitMQ and Celery responsibilities and boundaries;
- worker claiming, leasing, acknowledgement, and duplicate delivery;
- dependency ordering between diagnostic and profile-inference work;
- semantic retry and post-failure reconciliation;
- diagnostic effect boundaries and stale-worker fencing;
- durable ADK state required by external workers;
- cross-process notification of persisted diagnostic events;
- workload isolation, security, observability, testing, and rollout; and
- the architectural seams expected in the backend.

The initial job kinds are:

- `diagnostic_turn`; and
- `profile_inference`.

The same queue methodology may support later job kinds if each one defines its
own idempotency, retry, effect-boundary, and terminal-state rules.

## 3. Non-Goals

This spec does not:

- make model calls, external effects, or tool execution exactly once;
- make RabbitMQ or Celery the source of product truth;
- use Celery result storage as application state;
- move public diagnostic events out of PostgreSQL;
- change the public turn-acceptance or SSE contracts;
- define final SQL DDL, package names, timeout values, or capacity settings;
- define a general domain-event platform, Kafka deployment, CQRS, or event
  sourcing;
- require Celery Canvas for the core workflow;
- expose internal job or Celery task identifiers through the public API; or
- make RabbitMQ or Flower administration screens authoritative recovery tools.

## 4. Canonical Language

### Background Job

A **background job** is the durable application-owned record of work BikeDoc
has accepted. It identifies the job kind and the stored domain object from
which the worker loads its inputs. It is the authority for whether work may
run, wait, retry, reconcile, or become terminal.

### Job Attempt

A **job attempt** is one claimed execution of a background job. Attempts retain
bounded operational history without replacing the job as the current source
of truth.

### Outbox Message

An **outbox message** is a committed intent to notify the broker that a job is
eligible for execution. It is created in the same PostgreSQL transaction as
the application change that requires the job.

### Delivery

A **delivery** is one receipt of a Celery task message by a worker. Multiple
deliveries may refer to the same job and do not necessarily create multiple
attempts.

### Lease

A **lease** is a time-bounded claim on a job. Its monotonic version is a fencing
token used to stop an expired or replaced worker from committing later effects.

### Effect Boundary

An **effect boundary** is the point after which replaying the whole job might
duplicate user-visible, durable, billable, or external effects. Every job kind
must define whether it has such a boundary and what recovery is allowed after
crossing it.

### Reconciliation

**Reconciliation** converts abandoned, inconsistent, or externally
dead-lettered work into the correct durable application state. It may release
dependencies, schedule an eligible retry, or record a terminal outcome. It
must apply the same safety rules as normal execution.

## 5. Decision Summary

| Area | Canonical decision |
| --- | --- |
| Source of truth | PostgreSQL job ledger |
| Atomic acceptance | PostgreSQL transactional outbox |
| Broker | RabbitMQ with durable quorum queues |
| Worker framework | Celery |
| Delivery guarantee | At least once |
| Broker payload | Versioned JSON with an opaque job ID and bounded envelope metadata |
| Celery result backend | Not used for product or job state |
| Semantic retry owner | PostgreSQL job service and scheduler |
| Duplicate handling | Resolve every delivery against the job ledger |
| Diagnostic post-effect failure | Reconcile; do not automatically replay the whole turn |
| Workload isolation | Separate diagnostic and profile-inference queues and workers |
| ADK session state | Durable PostgreSQL-backed storage |
| SSE wake-up | Cross-process notification with the PostgreSQL event table remaining authoritative |

RabbitMQ is a command transport in this design. Kafka is not the BikeDoc task
executor. A future retained event stream or analytics platform would be a
separate architectural decision.

## 6. System Invariants

1. When acceptance of work commits, the required job records and every
   immediately eligible outbox message must commit in the same transaction.
2. When acceptance rolls back, no executable job notification may be
   published for that work.
3. RabbitMQ messages identify PostgreSQL jobs; they do not contain the work's
   authoritative inputs.
4. PostgreSQL determines whether a delivered job may run, wait, no-op, retry,
   reconcile, or become terminal.
5. Every consumer must assume that a message can be delivered more than once.
6. Delivery of a completed or terminal job must be an acknowledged no-op.
7. A job may have at most one valid execution lease at a time.
8. A stale lease holder must not commit application effects after its lease is
   replaced.
9. A diagnostic turn must not be automatically replayed after its effect
   boundary is crossed.
10. A queue acknowledgement must occur only after the durable outcome that
    makes acknowledgement safe has committed.
11. RabbitMQ availability must not be required to commit otherwise valid
    application work; unpublished outbox messages wait for recovery.
12. Public diagnostic events remain the authoritative history presented to
    clients.
13. Broker messages, metrics, and routine task logs must not contain
    user-authored content or secrets.
14. Diagnostic and profile-inference capacity must be independently deployable
    and scalable.

## 7. Architecture

```text
                        PostgreSQL
              +---------------------------+
              | product records and events|
              | background jobs/attempts  |
              | outbox messages           |
              | durable ADK sessions      |
              +-------------+-------------+
                            ^
             atomic accept  | claim and persist outcome
                            |
Client -> FastAPI API ------+------ Celery workers
             |                         ^
             | SSE reads events        |
             v                         |
      cross-process wake-up             |
                                      RabbitMQ
                                         ^
                                         |
                              outbox dispatcher
                                         ^
                                         |
                                     PostgreSQL
```

### 7.1 Component Responsibilities

The API validates and accepts product work. It creates jobs and outbox rows but
must not publish directly to Celery from request handlers.

The background-job service owns job creation, claims, leases, state
transitions, retry eligibility, dependency release, effect-boundary policy,
and reconciliation. Transport-specific behavior must not leak into routes or
domain services.

The outbox dispatcher claims committed outbox rows, publishes through a narrow
publisher interface, waits for publisher confirmation, and records publication
outcomes. Multiple dispatchers may run concurrently.

RabbitMQ durably transports execution notifications and applies finite
delivery and dead-letter policies. It does not decide product or job state.

Celery tasks are thin adapters. They accept an opaque job ID, establish a
worker-owned resource lifecycle, and call the background-job executor.

Workers load all authoritative input from PostgreSQL. They must not trust
message fields for ownership, authorization, job kind, or current state.

A scheduler or reconciliation process makes due retryable jobs publishable,
recovers expired leases, releases satisfied dependencies, and correlates dead
letters with job state. It may initially share a process with the dispatcher
as long as the responsibilities remain distinct.

### 7.2 Backend Boundaries

The backend should expose small internal interfaces for:

- background-job state and policy;
- job and attempt persistence;
- outbox persistence;
- message publication;
- worker execution composition; and
- event wake-up notification.

RabbitMQ and Celery are provider or worker adapters behind those interfaces.
Route handlers must not call `delay`, `apply_async`, or RabbitMQ clients.

The final package layout may follow implementation review, but any new or
materially changed module responsibilities and dependency directions must also
be recorded in `apps/api/ARCHITECTURE.md`.

## 8. Durable Acceptance and Dependencies

### 8.1 Turn Acceptance

For a newly accepted diagnostic turn, one PostgreSQL transaction must:

1. validate the request and current product state;
2. persist the accepted turn and its initial public event;
3. update the repair session as required by the diagnostic specs;
4. create one `diagnostic_turn` job in an executable state;
5. create that job's initial outbox message; and
6. when applicable, create one `profile_inference` job in a blocked state.

The transaction either commits all required records or none of them.

An idempotent HTTP replay must return the existing accepted result without
creating another logical job. A database uniqueness or stable deduplication
constraint must provide the final race-safe guarantee.

### 8.2 Dependent Work

Profile inference triggered by the turn should be represented at original
acceptance time and depend on the diagnostic job. This retains durable
knowledge of all required work even if a later process exits.

When the dependency reaches an allowed terminal state, the same transaction
that releases the profile job must create its outbox message. Reconciliation
must also release a blocked job whose dependency already satisfies the release
policy.

The precise set of diagnostic terminal states that releases profile inference
is a product-policy detail to be finalized before implementation. It must be
explicit and deterministic rather than inferred from Celery task completion.

## 9. Durable Records

This section defines logical requirements, not final SQL DDL.

### 9.1 Job Ledger

Each background job must retain enough information to determine:

- its stable identity, kind, and target domain record;
- any prerequisite job;
- its current state and earliest eligible execution time;
- its attempt count and configured attempt limit;
- its current lease owner, expiry, and fencing version;
- whether and when its effect boundary was crossed;
- its first start and terminal completion times; and
- its latest bounded, redacted error category.

The durable lifecycle must distinguish at least:

- blocked work waiting for a dependency;
- queued work eligible for execution;
- running work with a lease;
- retryable work waiting for its next eligibility time;
- succeeded work;
- interrupted work that may have effects and cannot be automatically replayed;
- dead work that is not eligible for another automatic attempt; and
- cancelled work.

Names and storage representation may be refined during schema design, but the
semantic distinctions must be preserved.

### 9.2 Attempts

Each execution claim must have a durable attempt record containing its job,
monotonic attempt number, lease version, bounded worker identity, transport
correlation identifier, timing, outcome, and redacted error category.

Attempt records are operational history. Celery task state must not replace
them.

### 9.3 Outbox

Each outbox record must contain:

- a stable message identity;
- a versioned message type;
- the target job ID;
- an allowlisted routing destination;
- minimal versioned envelope data;
- publish eligibility and attempt state;
- a dispatcher lease;
- publisher-confirm completion time; and
- a bounded transport failure category.

The schema must support intentional later notification generations for retries
while preventing accidental duplicate creation for the same generation.

Retention and archival periods are operational policy and are intentionally
not fixed by this version.

## 10. Publication and Broker Contract

### 10.1 Transactional Outbox

PostgreSQL and RabbitMQ do not share a transaction. The API must therefore
write an outbox record instead of publishing directly.

The dispatcher must:

1. claim due outbox rows safely across replicas;
2. publish persistent Celery-compatible messages to a durable exchange;
3. require a RabbitMQ publisher confirmation;
4. record confirmed publication in PostgreSQL; and
5. retry unconfirmed publication with bounded backoff and jitter.

The dispatcher may publish the same message more than once if it crashes after
broker confirmation but before recording that confirmation. This is expected;
worker deduplication must make it safe.

### 10.2 Message Envelope

Messages must use JSON and contain only:

- a schema version;
- a stable message ID;
- an opaque job ID;
- bounded routing or validation metadata; and
- a creation timestamp when required for operations.

Prompts, assistant output, artifact paths, bike profile data, access tokens,
email addresses, and raw user IDs must not appear in the broker payload or
headers.

The publisher must emit the pinned Celery task protocol through Celery/Kombu
or another deliberately compatible implementation. A custom JSON body placed
on a Celery queue is not sufficient merely because it contains a job ID.

Unknown message versions, task names, job kinds, or routing keys must fail
closed and be surfaced for reconciliation.

### 10.3 RabbitMQ Topology

Production must use durable, versioned exchanges and durable quorum queues.
Diagnostic and profile-inference work must use separate queues and worker
deployments. A finite broker delivery limit and a dead-letter path are
required.

Production quorum queues must run on a topology that provides broker-node
redundancy. A single-node RabbitMQ service is acceptable for local development
but does not satisfy the production availability requirement.

Exact exchange names, queue names, node counts, delivery limits, and hosting
provider are deployment decisions to be recorded before rollout.

Dead-lettered messages are operational evidence, not authoritative job state.
They must be correlated back to the PostgreSQL job and reconciled according to
job policy. Operators must not blindly requeue diagnostic messages from broker
or Celery administration tools.

## 11. Execution, Claims, and Acknowledgement

### 11.1 Delivery Resolution

For every delivery, the worker must resolve and claim the job in PostgreSQL
before executing domain behavior. Resolution must produce one of these
outcomes:

- a terminal job becomes an acknowledged no-op;
- a due queued job receives a new lease and attempt;
- a valid existing lease prevents concurrent execution;
- an expired pre-effect lease becomes eligible for controlled recovery;
- an expired post-effect lease is reconciled as interrupted;
- an unsatisfied dependency remains blocked without consuming an execution
  attempt; or
- an invalid or inconsistent job is recorded and routed to terminal handling.

The broker delivery is not proof that the job is eligible to execute.

### 11.2 Leases and Fencing

Long-running attempts must renew their leases. Lease expiry alone does not stop
a paused worker, so each lease must also carry a monotonic fencing version.

The worker must pass an immutable execution context containing the job,
attempt, and lease version into effect-producing application paths. Durable
event appends and state-mutating operations must verify the current fence in
the same transaction as their write. A stale worker must stop without
committing additional product effects.

External systems that cannot participate in the fenced transaction require an
idempotency key or a durable reservation before invocation.

### 11.3 Acknowledgement

Late acknowledgement is required. A worker may acknowledge a delivery only
after it has committed one of the following:

- successful completion;
- a terminal no-op decision;
- a scheduled application-owned retry;
- an interrupted, dead, or cancelled outcome; or
- another durable state that makes loss of that delivery safe.

Abrupt worker loss before acknowledgement may cause redelivery. All execution
paths must remain correct under that condition.

## 12. Retry and Recovery Policy

The PostgreSQL job service owns semantic retry eligibility, attempt counts, and
delays. Broad Celery automatic retry of arbitrary exceptions must not be used.
Celery or broker redelivery may handle a short-lived transport condition, but
it must not become a second semantic retry policy.

Each job kind must classify failures as retryable, interrupted, or terminal.
Retryable failures must be committed with their next eligible time before the
current delivery is acknowledged. A scheduler then creates a new outbox
generation when the job becomes due.

### 12.1 Diagnostic Turns

The diagnostic effect boundary must be persisted with a lease-version
compare-and-set immediately before entering execution that may invoke ADK,
emit assistant events, call a mutating tool, incur non-idempotent external
effects, or otherwise make whole-turn replay unsafe.

Failures before that boundary may retry within a bounded policy. Failures
after it must not automatically replay the whole diagnostic turn. They must be
reconciled to `interrupted`, update the diagnostic product state, and append
the required public recovery or terminal event exactly once.

Transparent replay after partial diagnostic effects is out of scope until
every relevant effect has finer-grained checkpointing and idempotency.

### 12.2 Profile Inference

The generic job is the durable execution envelope for profile inference. The
domain-specific inference run remains the authoritative inference result and
may retain its own domain state.

Implementation must define one mapping between job attempts and inference
attempts and one owner for retry scheduling. Nested independent retry loops
must not multiply provider calls beyond the intended policy.

### 12.3 Reconciliation

Reconciliation must cover at least:

- expired leases;
- confirmed or suspected duplicate deliveries;
- due retryable jobs with no current outbox notification;
- blocked jobs whose dependencies are already satisfied;
- outbox records abandoned by a dispatcher;
- broker dead letters; and
- application records left inconsistent by process failure.

Recovery commands and automated reconciliation must use the same eligibility
and effect-boundary rules. Administrative convenience must not bypass job
safety policy.

## 13. Worker Runtime and Resource Ownership

Celery workers should initially use separate prefork deployments with
prefetch kept conservative so queued diagnostic work is not reserved far ahead
of available capacity. Tasks must use JSON, ignore Celery results, and apply
late acknowledgement and lost-worker redelivery settings consistent with this
spec.

BikeDoc's database, provider, and ADK clients are asynchronous. A worker child
must create or acquire async resources within a lifecycle owned by that child.
Async engines, pools, event loops, and clients must not be created in the
Celery parent and inherited through `fork`, or reused across incompatible event
loops.

The first implementation may use a per-task async resource container because
its lifecycle is easy to reason about. A child-process-owned long-lived loop
and container may replace it after measurement, provided the executor
interface and safety properties remain unchanged.

Provider timeouts, Celery soft and hard time limits, job leases, heartbeat
cadence, graceful shutdown periods, and concurrency must be configured as one
coherent timing policy. Exact values require compatibility and load testing.

## 14. Cross-Process Prerequisites

### 14.1 Durable ADK Sessions

Diagnostic workers must use a PostgreSQL-backed ADK session service. A
process-local session service must not be enabled with external worker
execution.

The selected adapter must support access from multiple API and worker
processes, preserve all required BikeDoc session state, and have an explicit
schema migration, versioning, retention, and resource-lifecycle policy.
BikeDoc-managed migrations or an isolated and tightly pinned ADK schema are
preferred over uncontrolled runtime schema creation.

### 14.2 Diagnostic Event Wake-Ups

The PostgreSQL diagnostic event table remains the source of truth for SSE.
When a worker commits an event, API processes must receive a cross-process
wake-up and query for events newer than each subscriber's last delivered
sequence.

The initial implementation should use PostgreSQL `LISTEN`/`NOTIFY` with a
low-frequency polling fallback. Notifications are lossy hints and must never
replace sequence replay. A missed notification may delay a live update, but it
must not lose an event.

## 15. Security and Privacy

The durable queue must:

- use TLS for production broker connections;
- keep broker and management endpoints on private networks;
- use environment-specific virtual hosts and least-privilege credentials;
- keep broker credentials in the deployment secret store and redact broker
  URLs from logs and settings representations;
- accept JSON only and reject executable serializers such as pickle;
- allowlist task names, message versions, routing keys, and job kinds;
- derive authorization relationships and inputs from PostgreSQL rather than
  trusting message metadata;
- retain only bounded, redacted failure details; and
- follow the diagnostic telemetry privacy rules for identifiers, logs, traces,
  and metrics.

## 16. Operations and Observability

PostgreSQL job and attempt records are the primary operational view. Flower and
RabbitMQ administration tools may supplement that view but must not decide
whether product work succeeded or is safe to retry.

BikeDoc must provide an application-owned inspection path showing job state,
attempts, dependency state, effect-boundary state, lease state, and bounded
recent error codes. Any recovery path that can retry work must enforce the
canonical eligibility rules.

Operations must measure at least:

- pending outbox count and oldest pending age;
- publish confirmations, failures, and latency;
- ready and unacknowledged messages by fixed queue name;
- job enqueue-to-start latency, execution duration, and bounded outcomes;
- active and expired leases, heartbeat failures, and stale-worker rejections;
- retry, interruption, duplicate no-op, and dead-letter counts;
- worker availability and forced termination;
- SSE wake-up latency and polling recovery; and
- PostgreSQL connection use across APIs, workers, dispatchers, and listeners.

Job, turn, session, user, task, and trace IDs must not be metric labels.
Structured logs and traces may use approved opaque correlation fields as
allowed by the telemetry spec, but must not include user content or full broker
messages.

Alerts must detect material outbox lag, diagnostic queue latency, unavailable
worker capacity, publisher-confirm failures, broker quorum or resource alarms,
dead-letter growth, abnormal lease expiry or interruption rates, and database
connection saturation.

## 17. Verification Requirements

Unit tests must exercise job policy and dispatch behavior through fakes without
requiring RabbitMQ, PostgreSQL, providers, or a real model.

Integration tests must use real PostgreSQL and RabbitMQ with fake model and
provider behavior. Before production rollout, tests must prove at least:

1. atomic creation and rollback of product, job, dependency, and outbox state;
2. HTTP idempotency cannot create duplicate logical jobs;
3. publisher-confirmed Celery protocol messages survive the configured broker
   restart scenario;
4. a dispatcher crash in the publish-confirm window cannot duplicate product
   effects;
5. a completed job handles duplicate delivery as a no-op;
6. a valid lease prevents concurrent execution;
7. a stale worker cannot append events or commit mutating writes;
8. a pre-effect crash can recover within policy;
9. a post-effect diagnostic crash becomes interrupted without another ADK
   invocation;
10. semantic retry counts and delays come from PostgreSQL;
11. dependent profile work releases once under the configured policy;
12. a profile backlog cannot consume diagnostic worker capacity;
13. malformed or unknown messages fail closed and reach reconciliation;
14. worker-created events wake another process's SSE readers;
15. missed notifications recover through polling and sequence replay;
16. graceful and forced worker shutdown produce safe durable outcomes; and
17. broker payloads, logs, and metrics contain no prohibited content.

Compatibility tests must validate the pinned Celery, Kombu, RabbitMQ, async
resource, and ADK session stack. Load tests must establish provider-safe
concurrency, database connection budgets, timing values, resource-container
overhead, broker-recovery behavior, and acceptable event latency.

## 18. Rollout and Cutover

Implementation should proceed in reviewable stages:

1. prove broker confirms, Celery protocol compatibility, worker async resource
   ownership, durable ADK sessions, and cross-process event wake-ups;
2. add the job ledger, attempts, outbox, policy service, and test adapters;
3. deploy the dispatcher, scheduler/reconciler, queues, and worker process
   types;
4. canary profile inference on the durable queue;
5. add diagnostic effect fencing and canary diagnostic execution; and
6. remove FastAPI `BackgroundTasks` from production execution after the durable
   path meets the verification requirements.

The routing decision must select exactly one executor for each logical job.
The in-process and Celery paths must never execute the same turn concurrently.

Rollback must drain or stop consumers, inspect active leases and effect
boundaries, preserve job and outbox state, and enable any fallback executor
only for work proven safe to run. Changing a feature flag while old consumers
continue running is not a complete rollback procedure.

## 19. Deferred Implementation Decisions

The following details are intentionally left for implementation planning,
compatibility spikes, capacity testing, or later ADRs:

- managed versus self-operated RabbitMQ and the exact production topology;
- concrete exchange, queue, routing-key, and dead-letter policy names;
- pinned Celery, Kombu, RabbitMQ, and ADK versions;
- exact database columns, constraints, indexes, and retention periods;
- concrete package and process layout;
- dispatcher batch size, polling interval, and replica count;
- retry limits, delay curves, lease duration, and heartbeat cadence by job kind;
- provider timeouts, Celery time limits, worker concurrency, and autoscaling;
- the profile-inference dependency release states;
- per-task versus per-child async resource containers;
- the durable ADK adapter and schema-ownership mechanism;
- exact operator command surface and authorization model; and
- deployment-provider-specific secrets, monitoring, and runbooks.

These decisions may tune the implementation but must not weaken the invariants
or change the ownership boundaries defined by this spec without updating the
canonical document.

## 20. Completion Criteria

The durable queue methodology is implemented when:

- every accepted asynchronous operation is represented by durable job state;
- acceptance and initial publication intent are atomic;
- API, dispatcher, broker, and worker restarts cannot silently lose accepted
  work;
- broker downtime accumulates recoverable outbox work instead of creating a
  database/broker dual-write gap;
- duplicate deliveries cannot duplicate committed product effects;
- diagnostic pre-effect failures may retry within policy while post-effect
  failures cannot trigger automatic whole-turn replay;
- stale workers are fenced from further application writes;
- ADK state and SSE delivery work correctly across processes;
- diagnostic and profile-inference capacity are isolated;
- job inspection, reconciliation, alerts, and failure-injection tests are in
  place;
- production messages are JSON-only and contain no user-authored content or
  secrets; and
- FastAPI `BackgroundTasks` is no longer used for production turn execution.
