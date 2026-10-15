# Queue inspection and telemetry

`task jobs:inspect -- --filters '{"kind":"diagnostic_turn","terminal":false,"minimum_age_seconds":60,"limit":50}' --transport`

The internal command emits JSON with `postgresql_truth` and
`jetstream_transport_observations`. It exposes no HTTP endpoint. Omit
`--transport` for database-only inspection. Run with the normal API configuration
(or directly use `python -m bike_doc_api.operations.inspection` from the API
package). A database role restricted to SELECT is appropriate for operators.
The command itself executes `SET TRANSACTION READ ONLY`, explicitly projects
columns without loading input JSON or joining domain tables, and disposes its
connection on failure or interruption. Invalid arguments and failures emit fixed
messages without echoing their values. It never creates topology or starts
maintenance.

Filters are an extra-fields-forbidden object: `kind`, `workload`, `version`,
`state`, `terminal`, `minimum_age_seconds`, `limit` (1–200, default 50), and
`after`. Pass the returned opaque `next_after` as `after` to continue in ascending
job-ID order. Kind/workload/state values are allowlisted; version is 1–32767,
age is 0–315360000 seconds. Pages are bounded, independent database snapshots,
so concurrent state changes may change filter membership between pages.

Rows show opaque job identity, registered definition (unrecognized strings are
replaced by `unregistered`), state, attempt/limit, desired/confirmed generation,
effect-boundary timestamp, eligibility, publication confirmation, first/current
start, execution deadline, terminal/creation/update timing, and bounded job and
transport error categories. They exclude inputs, logical deduplication keys,
execution/publication/effect tokens, user identities, and domain content.

PostgreSQL owns accepted intent, eligibility, attempts, effect safety and product
outcomes. Broker counts and advisories are transport observations only. Stored
messages are not unfinished jobs; a terminal job can still have an unacknowledged
notification, and an eligible job can exist with no broker message. A failed
broker inspection returns `status: unavailable` alongside the completed database
view. It does not infer failed jobs or authorize replay.

## Metric contract

All instruments use the process-wide OpenTelemetry provider, meter
`bike_doc.queue`, and prefix `bike_doc.queue.`. The deployment environment is the
existing resource attribute. Counters are event observations, not exactly-once
audit records. Histograms use seconds. Gauges represent the latest local sample;
series freshness and aggregation across replicas must be considered.

| Suffix | Type / unit | Exact dimensions | Meaning |
| --- | --- | --- | --- |
| publication_pending | gauge / jobs | none | Rows with desired > confirmed generation |
| publication_oldest | gauge / seconds | none | Age since acceptance of oldest row awaiting publication (conservative upper bound for later generations); zero when empty |
| publication_ack | histogram / seconds | workload | Broker round-trip through durable publish acknowledgement |
| publication | counter | workload, publication | confirmed, duplicate, unavailable, timeout, rejected |
| transport_messages | gauge / messages | consumer, transport | stored for logical work_stream; pending, ack_pending, redelivered, waiting for each consumer |
| queue_latency | histogram / seconds | kind, version, workload | Acceptance to execution start, including retries |
| execution_duration | histogram / seconds | kind, version, workload | Runtime execution through outcome persistence/settlement or loss |
| outcome | counter | kind, version, workload, outcome | succeeded, retrying, dead, interrupted after commit (including normalized recovery) |
| resolution | counter | workload, resolution | executable, terminal/stale no-ops, early/running, recovery_wait, prohibited, missing, definition_failure, exhausted, stale_token |
| active | up/down counter / executions | workload | Local invoked handlers, including cancellation-resistant handlers until they finish |
| recovery | counter | kind, outcome | Committed deadline recovery by final persisted outcome |
| worker_available | gauge / 0 or 1 | workload | Successful fetch capability, fetch failure, or shutdown |
| worker_health | counter | workload, health | ready (successful fetch heartbeat), fetch_failed, unsettled, graceful, forced |
| advisory | counter | consumer, advisory | max_deliver or terminated; observation only |
| sse_latency | histogram / seconds | none | Persisted event timestamp to reader wake-up query result |
| sse_recovery | counter | none | Poll cycle recovered persisted events without a hint |
| pool | gauge / connections | role, pool | QueuePool size, checked_out, nonnegative overflow; roles api, worker, sse |
| maintenance | counter | maintenance | notification_repair, cleanup_scanned/deleted, publication/reconciliation/cleanup_scan_failed, observation_failed |

`core/queue_contract.py` is the executable name/dimension/value contract.
Definitions are the two initial kinds, version `1`, and fixed diagnostic/profile
workloads. Consumer labels are logical `diagnostic`, `profile_inference`, or
`work_stream`, never configured arbitrary broker names. All metric attribute
sets must match the contract exactly. Unknown values, extra keys, non-finite
numbers and invalid negative values are dropped before sinks see them. Adding a
registered definition requires a deliberate contract update and cardinality test.
Identifiers, subjects, error strings, URLs, user IDs, email addresses, prompts,
artifact paths, model inputs and broker bodies/headers are prohibited attributes.
No queue-specific spans or identifier correlation are introduced; structured
`queue_observation` events contain only validated counter names and dimensions.
Existing opaque OpenTelemetry trace correlation can accompany these events.

API-hosted maintenance samples publication pressure and broker observations in
bounded failure-isolated operations before claiming publication work. Its
monitoring connection also subscribes to fixed maximum-delivery and termination
advisory subjects. Worker hosts use the same monitoring adapter. Advisory bodies
are size-bounded and only stream/consumer identity is checked; payload fields,
reason text, sequence and IDs are never emitted. The adapter has no job-store or
product-repair dependency. Multiple observers may count the same advisory;
these are alert inputs, not authoritative transition counts.

Worker availability combines recent successful fetch observations with pending
work and consumer waiting/ack counts. Abrupt SIGKILL cannot emit a final sample:
missing heartbeat/freshness detects a vanished process; `forced` means local
shutdown exceeded its grace period. Alert thresholds and delivery belong to
Task 4.3. Pool instrumentation uses driver-exposed QueuePool measurements only,
without querying server sessions or logging connection identifiers. API
maintenance and SSE share the API pool: the sse role is a sampled view of that
pool, not additional connections to sum. Checkout/checkin sampling also observes
short-lived worker and API pressure. No pool tuning is performed here.

Telemetry validation/export failures cannot change outcomes or settlement.
Transport observation failure cannot change job rows. Cancellation still
propagates normally. Durable recovery uses PostgreSQL policy and execution
fencing regardless of transport samples.

## Verification

Run `task format`, `task check`, then `task test:observability`. The latter starts
and migrates disposable PostgreSQL 16 and pinned JetStream, runs shared worker, diagnostic worker,
maintenance and cross-process SSE tests, and removes volumes/containers.
It covers publication failures and recovery during broker stop/start, inspectable
backlog during outage, publication acknowledgements, read-only column projection,
real redelivery/maximum-delivery/termination advisories without job mutation,
execution latency/outcomes, deadline recovery, connection measurements, and SSE
hint/poll recovery across processes. Unit recorders exhaust bounded dimension
combinations, reject sentinel attributes, exercise malformed filters and
interruption, and verify actual SDK metric export and structured events with
private sentinels. Existing envelope/header privacy tests remain in the worker
and maintenance suites. These tests use fake handlers/providers, not model calls.


| Task #154 acceptance | Evidence |
| --- | --- |
| Bounded fields without input/domain reads | Explicit projection, forbidden-column/query tests, command snapshots and real CLI privacy check |
| PostgreSQL truth separate; broker unavailable | Separate output objects; real broker stop/start inspection |
| Read-only success, malformed input and interruption | Read-only transaction; validation-before-connect, cancellation/disposal tests; real unchanged job state |
| Full queue metric coverage and finite dimensions | Executable metric contract, exhaustive recorder/SDK assertions, runtime/maintenance/SSE/pool hooks |
| No private content or identity labels | Sentinel tests over inspection, envelopes/headers, structured events, spans and metric attributes |
| Advisories cannot mutate jobs/products | Monitoring-only dependency boundary; real max-delivery and termination tests retain queued state/zero attempts |
| Export failures cannot affect durable outcomes | Sink-failure worker test asserts committed success precedes ACK; independent inspection sessions |
| Final recovery outcomes | Normalization test and real diagnostic completion-before-settlement recovery returns succeeded |
| Production-like degradation signals | Disposable integration target covers outage/backlog, latency, absent workers, recovery, advisories, SSE polling and concurrent connection samples |
| Required verification | `task format`, `task check`, then `task test:observability` |
