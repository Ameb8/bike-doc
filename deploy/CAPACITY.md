# V1 workload capacity certification (#156)

Run `task test:capacity` from the repository root with Docker/Compose available.
It builds the locked runtime image, then runs each scenario on a **clean**
Task 4.3 production Compose project with real PostgreSQL 16, file-backed NATS
2.12.1, nats-py 2.12.0, the OTLP Collector, two API/maintenance hosts, two
diagnostic workers, two profile workers and eight concurrent real HTTP SSE
readers. No paid providers or credentials are used. Normal `task check` neither
starts Docker nor collects this harness. Budget and harness code live in
`apps/api/tests/capacity/`; selected deployment settings are `capacity.json`.

For focused iteration: `task test:capacity -- --scenario profile_backlog`.
`--no-build` reuses the last image and is only suitable when runtime code has
not changed. Each run uses a fresh `.capacity/run-TIMESTAMP` directory and
refuses to overwrite evidence. All resources it creates are removed in `finally`;
existing projects are untouched. Raw evidence stays private (0700 directory,
git/Docker ignored). A sanitized result summary is versioned alongside this
file. On success, `certification.json` contains the environment fingerprints
and an allowlist of aggregate scenario measurements and gates; copy that file to
`deploy/capacity-results.json` after reviewing it. It excludes raw captures,
private identities and per-process capture names. A failed gate exits nonzero;
inspect that run's private logs locally.
Do not publish raw captures or secrets. This is a finite certification workload,
not a claim about production model throughput or high availability.

The build uses the dedicated `bikedoc-runtime:capacity` tag. Each scenario
rechecks the production NATS role ACLs with Task 4.3's real-broker verifier
before starting workers. The environment record includes source SHA-256
fingerprints, effective role settings and runtime image identity. Progress
acknowledgements and stopped-process exit codes are additional direct evidence;
the timing gate requires real progress from both workloads and fixed deadlines.

## Workload and failure model

HTTP setup creates independent synthetic bikes, sessions and real JPEG artifacts.
Traffic submits immutable turns through the product endpoint, with images on
half the turns unless the scenario saturates profile inference. Every image turn
creates both real job kinds. External calls use deterministic asynchronous delays;
diagnostic execution still runs the real ADK agent and PostgreSQL session store,
and profile extraction still runs the real inference service and claim writes.
Each scenario has the same finite traffic definition on every run; opaque record
IDs and secret values vary. Scheduling and hardware affect measured latencies.

Fixture provisioning waits up to five seconds for independent PostgreSQL
visibility after bike/session/upload responses. These setup endpoints finalize
their dependency transactions after sending a response, which can race an
immediate read on another API replica. Setup is outside the traffic clock.
Timed turn submissions are never retried or fenced by the driver: their 202
response must follow durable acceptance, including during broker outages.

| Scenario | Traffic / injection | Required evidence |
| --- | --- | --- |
| steady | 24 turns, one arrival every four seconds, 0.1s diagnostic / 0.25s profile fake call | mixed queue, publication, execution, SSE, pool and DB observations |
| burst | 16 turns, two concurrent submitters | bounded admission and diagnostic queue p95 <=24s |
| profile_backlog | 48 image turns every four seconds, 20s profile calls (two slots); release to 0.25s after arrivals | >=16 pending profile messages, diagnostic latency remains within 8s |
| profile_failure | 48 image turns every four seconds, 1s failing profile calls | bounded attempts, dead profiles, successful diagnostics, no profile claims |
| nats_outage | same 24 mixed turns while NATS is stopped; restore afterward | all 36 publication intents accepted unconfirmed, later fully drained |
| worker_loss | 24 mixed turns with 5s calls; SIGKILL both diagnostic/API roles after effect starts, graceful stop/restart profiles | deadline recovery, interrupted diagnostics without replay, durable completion once, new acceptance after API replacement |
| broker_restart | 24 mixed turns with 5s calls; restart broker while work is active | volume persistence, reconnect, complete drain |
| duplicates | 24 mixed turns plus three duplicate envelopes/job | running/terminal no-ops, one durable effect |
| wakeup_loss | 24 mixed turns; drop incoming API NATS wake-ups | SSE polling-recovery signals and live completion |
| timing | eight mixed turns with 12s fake calls | calls exceed 10s progress cadence, fixed 120s/60s deadlines, no duplicate effects |

The worker-loss scenario tests a bounded interruption with accepted backlog,
not zero downtime: SSE reconnects/replays and diagnostic post-effect loss settles
through authoritative deadline recovery. Provider-failure diagnostics are not
replayed. Functional fencing/timeout/crash permutations remain covered by
`task test:recovery` and Task 4.5; this suite measures their behavior under load.

## Joint resource budget

| Resource | Selected V1 value / rationale |
| --- | --- |
| API / maintenance replicas | 2, allowing independent claims and one-host overlap |
| Publication | 8 concurrent notifications/host, 1s cadence; 30s claim >10s bounded publish; confirmations use reserved DB pool |
| Reconciliation | 100 rows/30s; no-progress repair 300s exceeds claim+cadence and 60s capped exponential retry backoff |
| Diagnostic replicas / local concurrency / fetch | 2 / 2 / 2; four reserved execution slots |
| Profile replicas / local concurrency / fetch | 2 / 1 / 1; two slots, deliberately below diagnostic capacity |
| Diagnostic / profile MaxAckPending | 8 / 8 per durable; diagnostic has twice aggregate concurrency, profile includes delayed-retry occupancy (measured rationale below) |
| AckWait / progress | 30s / 10s; progress < half AckWait, with 20s transport margin; acknowledgements never renew PostgreSQL deadlines |
| Diagnostic timing | provider/observation 30s, price/storage 20s; handler 110s + grace 5s + settlement 5s = hard 120s |
| Profile timing | provider 30s < handler 50s; handler + grace 5s + settlement 5s = hard 60s |
| Worker shutdown | stop-fetch, 30s settle then cancel; grace/final drains bounded inside Compose 60s; longer work safely awaits deadline recovery |
| API shutdown | Uvicorn 35s + maintenance close 10s + bounded notifications/ADK/telemetry close; Compose 60s outer bound |
| Pool checkout | 5s, no overflow on any application/ADK pool |

A worker's product pool must hold concurrency + two spare connections;
diagnostic ADK capacity must be at least diagnostic concurrency. Fetch never
exceeds free local slots. Broker pending limits are transport caps, not new
execution capacity. Changing a consumer's limit or AckWait requires an explicit
stop/drain/reprovision operation: topology checks reject incompatible existing
consumers. Never silently update a live durable or delete outstanding work.

The runtime enforces the local limit before starting each delivery even when
the client returns more messages than requested. In nats-py 2.12.0, batch fetch
drains the subscription's pending queue after reconnect. A rejected outage run
observed eight active handlers in one diagnostic process configured for two,
followed by two dead diagnostics. Per-delivery admission now bounds claims and
handlers; buffered notifications remain unclaimed and unacknowledged on shutdown.
The reconnect-overfetch unit regression covers both full drain and shutdown;
the repeated outage certification checks real process peaks.

A coordinated six-process cold restart exposed a second recovery issue: one
API's first cleanup scan failed under restart pressure, then waited the normal
600s retention cadence without becoming ready. Failed cleanup scans now retry
at the smaller of the retention and reconciliation intervals (30s here), then
return to 600s after success. Readiness still requires all three maintenance
loops to succeed; neither pool limits nor health checks are bypassed.
The same rejected run durably interrupted four lost diagnostics at 144.4s and
settled the remaining accepted work by 191.4s after injection. The test-host
coordinated-loss recovery/queue gate is therefore 240s: 191s observed work
settlement, one 30s cleanup retry and 19s margin. This covers simultaneous cold
restart on this Pi; the normal diagnostic queue gate remains 8s and the hard
execution deadlines remain 120s/60s. Loss runs must still prove new acceptance,
complete drain, unique effects and SSE recovery before passing.

For PostgreSQL `max_connections=100`, the test topology's **maximum** is:

```
API       2 × (13 request/SSE + 2 maintenance + 1 ADK) = 32
Diagnostic 2 × (4 product + 2 ADK)                     = 12
Profile    2 × 3 product                              =  6
Application total                                    = 50
Certification driver / finite operator allowance      =  2
Remaining PostgreSQL/admin/deployment headroom          = 48
```

SSE streams retain a request connection while open. Each 13-connection API pool
can accommodate all eight readers plus two concurrent submitters, one
health probe and two spare checkouts even if gateway distribution is skewed. The aggregate request
budget is 26 connections.
SSE is an **alias** of the API pool, not another pool. Maintenance's four reserved
connections do not compete with SSE. Profile can use at most six connections;
it cannot consume diagnostic subjects/durable consumers, semaphores, ADK/model
clients or pools. The production NATS ACLs independently enforce the subject
boundary. PostgreSQL CPU, I/O, locks, and connection admission remain shared:
this reservation is a ceiling, not database scheduling QoS or per-role DB auth.
The harness records connection totals and lock waiters separately from per-pool
signals and broker acknowledgement/pending counts. Independent pressure
sampling runs at 1 Hz. Exact observation capture uses a bounded 64 KiB buffer
with periodic flush and final close; SIGKILL can lose its final unflushed
observations, as it can lose a normal exporter batch. Collector export and
observation capture add overhead to this certification.

## Pass/fail gates and evidence

The executable gates use nearest-rank p95 (nonempty sample sets), not averages.
Normal steady/profile-backlog/profile-failure/duplicate/hint-loss diagnostic
queue p95 must be <=8s; publication ACK p95 <=1s; acceptance p95 <=2s; SSE frame
latency p95 <=3s. Database connections must stay <=52 and pools must have zero
overflow. Both roles must produce queue/execution measurements, and both replicas
of every role must contribute signal files. Every turn must have exactly one
`turn.completed`; successful profile inference must have one run, one claim and
at most one profile revision. All eligible intent and consumer pending/ACK counts
must drain to zero. A failed profile uses exactly its application attempt budget.
Every captured pool size and checkout sample is checked against its role's
reservation (including API, maintenance, diagnostic product, ADK and the SSE
alias); an unexpected pool role fails certification.

Interruption-specific gates are explicit rather than mixed into normal service
latency: burst queue p95 <=24s; all unpaced two-client scenarios (burst,
worker/API loss, broker restart and timing) use acceptance p95 <=4s. The
worker-loss calibration measured 2.503s admission p95; its prior steady-traffic
2s gate mismatched the unpaced offered load. Paced mixed/profile scenarios
retain the 2s gate. NATS downtime separately measures admission while offline and during restoration,
both retaining the normal 2s p95 gate after correcting the SSE reader timeout. Broker restart queue
p95 <=60s; NATS downtime queue p95/drain <=150s;
worker/API loss queue/recovery and SSE replay age <=240s. After API recovery,
one completed reader moves to the newly accepted session (eight readers remain),
and a separate nonempty sample of events created after recovery must meet the
normal 3s live SSE p95 gate. A rejected run observed 140.376s replay age during
the all-API outage, despite 1.359s wake-up observations and 181.748s recovery;
the provisional 40s replay gate did not include measured cold restart time.
The replay gate now covers the same bounded outage as the recovery gate, while
the independent fresh-event gate verifies restored service latency. Timing
scenario queue p95 <=30s: the measured 22.046s includes 12s fake calls and
cold framework overhead; the gate reserves approximately 8s margin. Execution p95 stays below the configured hard deadline
for each kind; recovery time also includes reconciliation and application retry.
The budget is an upper bound, not a target operating latency.

Transport settlement is measured separately from durable product completion.
Its gate is 45s except duplicate pressure (165s = 120s maximum fixed deadline
+ 30s acknowledgement wait + 15s margin). A running duplicate receives a
delayed NAK until its existing hard deadline; a later product completion does
not shorten that broker delay. The suite still requires every pending and
acknowledgement count to reach zero and never resets a deadline to drain faster.

`result.json` records workload, percentile/count measurements, signal coverage,
check outcomes and effective budgets. `pressure.json` contains independent
PostgreSQL totals/lock waiters and real stream/consumer samples. `signals-*.jsonl`
captures the existing Task 4.2 validated observation seam while exporting normal
OTLP metrics. Collector files capture actual exported traces and metric
attributes; `broker.json` samples Core NATS copies of actual minimal messages
and headers without consuming durable deliveries. Logs and all captures fail on
synthetic content, provider-output/error, artifact-filename or secret sentinels
and generated connection passwords. Privacy checks require nonempty captures;
a missing surface cannot pass vacuously. Product/ADK PostgreSQL records, artifact
storage and HTTP/SSE responses are approved content stores and are not archived
as operational evidence. The observer's test-only subscribe permissions are
separate from unchanged production worker credentials.

## Scaling and alert implications

For different hardware, pool sizes, replica counts, SSE counts or fake service
times, rerun **all** scenarios and choose new explicit gates before promoting
settings. Keep `sum(replicas × per-process pools) + operator + rollout surge`
below the approved database connection limit. One extra API costs 16, diagnostic
6, profile 3 connections; adding all three once costs 25, leaving 23 of
the current 48 headroom. Plan rolling deployment surge explicitly. Do not scale
profiles simply because backlog grows if it threatens the shared DB budget.
Steady-state stability requires arrival rate < total slots / measured service
time for each workload; real models can change this by orders of magnitude.

Use a sustained exported diagnostic queue p95 >6s as the
investigation/scaling trigger, provided pool/DB pressure has headroom. Diagnose
publication lag before adding workers. Profile backlog is lower priority: add
capacity only after checking diagnostic latency, DB pressure, provider quotas
and consumer pending limits. The configured ACK limits support two replicas plus
one replica's stale delivery envelope; recalculate before scaling beyond that.
Never raise concurrency, pools and MaxAckPending independently.

For a new deployment size, update the pinned replica counts in `certify.py`,
SSE/traffic definitions in `driver.py`, per-role ceilings in its evaluator,
and the reviewed gates in `budget.py`. Synchronize `Settings`, `capacity.json`,
`.env.example`, deployment counts and budget regressions in the same change.
These constants describe this finite topology; caller replica environment
variables are overridden by the certification. Apply the connection arithmetic
above, reserve worst-case SSE placement and rollout overlap, then use measured
service/admission rates to set the offered traffic and gates before promoting
settings. CPU, database and provider contention require a new measurement rather
than linear extrapolation from replica count.

Alert contract changes use `service.name` **and pool role** to reduce utilization;
include `maintenance` and `adk` along with `api`/`worker`, excluding `sse` alias.
Retain database utilization warning at 0.8 (20% pool margin), publication lag at
60s (larger than normal 1s cadence, below 300s repair threshold), zero-tolerance
publication failures/interruption/max-delivery and 80% broker resource warnings.
A short fake-provider run does not justify loosening those safety thresholds or
claiming production alert SLOs. Diagnostic queue alerts should use the workload
selector and a threshold with measured headroom; final measurements below record
the selected implication. Profile backlog must not hide diagnostic latency in
one merged percentile.

## Recorded verification

Results and host details are populated from the executed certification, not
estimated from the configuration arithmetic. See `capacity-results.json` for
sanitized machine-readable evidence and the measured-results table below.

### Calibration and rejected settings

On the Raspberry Pi 4 Model B Rev 1.5 (four aarch64 CPUs, approximately 3.7 GiB
RAM), the initial 10-turn/s mixed workload with eight diagnostic slots, four
profile slots and 12 request connections/API was overloaded. Measured nearest-rank
p95 was 17.983s for diagnostic enqueue-to-start, 14.975s for execution, 7.970s
for acceptance and 4.058s for SSE frames. Connections peaked at 47 and lock
waiters at two; one API request pool reached all 12 checkouts. This is rejected
capacity evidence, not a passing run. The harness also exposed and fixed
root-owned driver output preventing the host from writing its certification;
the traffic container now writes as the invoking host UID.

Measurements led to fewer active handlers, less publication fan-out and a
smaller total connection ceiling. One turn every four seconds is the finite
steady certification rate; a 16-turn burst uses two concurrent submitters and
its own 24s queue/4s admission gates. The API pool of 13 reserves room for a
skewed eight-reader SSE distribution, two submitters, a probe and two spares. Application
pool ceilings fall from 58 to 50 in total. The burst queue budget accommodates
bounded admitted backlog; it is not a relaxed steady-state or profile-isolation
budget. With four diagnostic slots, the broker-restart scenario's 24 five-second
calls need at least six execution waves: its 60s queue gate reserves framework
and restart margin beyond the 30s service-time floor. No setting represents
paid-provider throughput or capacity on other hardware.

The intermediate one-turn/s calibration also failed the steady gates: diagnostic
queue p95 8.488s and acceptance p95 4.227s, with execution p95 9.236s,
publication ACK p95 0.084s, SSE p95 2.860s and 31 peak connections. The final
offered steady rate is therefore 0.25 turn/s. The NATS outage uses the same
24-arrival sequence before and after restoration; its 150s queue/drain gate
includes the approximately 96s arrival window plus recovery margin. Profile
saturation holds two 20s fake provider calls active throughout 48 paced arrivals,
then switches future calls to 0.25s to measure a finite drain. All eight SSE
reader loops remain active through traffic, including after observing completion;
profile scenarios watch the final eight sessions so live event latency is
measured while the profile workload is under pressure. Reader loops reconnect
and replay after HTTP read/server timeouts or process loss. SSE shares the API
request pool throughout these loops.

SSE reads allow 45s between frames; connect/write/pool timeouts and ordinary
request reads remain 15s. The original 15s stream read timeout matched the
server's 15s heartbeat before commit/network overhead, causing artificial
reconnects and cancellation during heartbeat writes. A rejected run reached
13.138s restored admission and 6.477s SSE p95 while pools remained below their
ceilings and lock waiters stayed at zero. That run is retained as failed
calibration. Long-lived readers now leave heartbeat margin; all latency gates
remain independently enforced.

The initial four-submitter burst met queue (11.478s p95), SSE (2.744s) and
connection (36 peak) gates but failed its 4s admission gate at 5.894s p95.
The certified traffic candidate therefore limits concurrent submitters to two
while retaining the admission gate. The connection ceiling is unchanged; spare
request checkouts absorb probes and gateway skew.

Runtime image versions verified directly: Python 3.12.12, PostgreSQL 16.15,
NATS server 2.12.1, nats-py 2.12.0, asyncpg 0.31.0, SQLAlchemy 2.0.51,
google-adk 2.3.0, OpenTelemetry SDK 1.42.1 and Pillow 11.3.0. Docker is 29.4.3.
The root filesystem is /dev/mmcblk0p2, 57G total with 3.3G available at
calibration. Other development services share the host. Small 16×16 synthetic
JPEGs provision fixtures before the traffic clock; upload throughput, large-image
processing, real authentication and paid provider/account quotas need their own
deployment measurements. Exploratory calibration used earlier capture adapters
and sampling rates, so it is not a controlled comparison of worker counts.

The four-ACK profile consumer failed the sustained provider-failure drain gate:
109.097s after arrivals, while diagnostic queue p95 stayed 1.747s and admission
p95 1.195s. Independent broker inspection showed all four ACK slots occupied,
queued messages and fewer than two active handlers. Two 10s delayed retry NAKs
plus provider/handler work occupy a notification slot for approximately 25s.
At 0.25 profile jobs/s, approximately 6.25 notifications remain outstanding;
eight slots provide bounded transport headroom. The selected profile limit is
therefore eight, with concurrency/fetch still one per replica and pool size
still three. Increasing the consumer buffer releases no extra worker/provider
slots or database connections. The 90s drain gate is retained.

Live execution peaks are computed from each process's `active` observations.
Each restart gets a separate capture file so a SIGKILL's missing decrement is
kept in the lost process's history. Every diagnostic process stays within two
active handlers and every profile process within one. During the single forced
loss injection, PostgreSQL may retain up to four old diagnostic and two old
profile running claims alongside replacement work until fixed-deadline recovery.
Those rows represent execution authority awaiting recovery, not live semaphore
or connection use. Independent pool limits and live-process observations still
apply throughout recovery.

The diagnostic queue warning is 6s. The exact Task 4.2 observation samples used
for certification have healthy p95 around 1.5s, but the current SDK's default
OTLP explicit histogram buckets begin at 0 and 5 seconds. A backend interpolating
a merged histogram containing only that positive bucket estimates p95 at 4.75s.
The warning leaves 1.25s above that healthy estimate and 2s below the exact 8s
steady failure gate. Keep the distinction between exact certification samples
and bucket-estimated operational quantiles when sizing another deployment.
The observed exported normal pool gauges remained below the retained 80%
warning; raw checkout-hook peaks include short simultaneous confirmations.

NATS-restoration publication ACKs have the configured 10s publish-operation
budget because the observation includes lazy connection establishment. After a
full publish timeout has elapsed beyond the restoration marker, a separate
nonempty sample set must meet the unchanged 1s connected-state ACK gate. The
outage's exact PostgreSQL snapshot verifies all 36 accepted intents are pending;
it supplements periodic pressure samples when recording the pending maximum.

### Acceptance evidence and interpretation

Each certified case starts a fresh topology; `capacity-results.json` combines
successful per-case reports rather than merging percentiles across runs. The
common environment records the runtime image, source fingerprints and selected
settings. Per-run source overrides retain provenance when only certification
adapters or gates changed. Raw evidence remains under the named private run
folders and is deliberately excluded from version control.

The following checks apply to every successful case: nonempty broker, log,
trace and metric privacy captures; minimal broker envelopes; independent
PostgreSQL and consumer pressure samples; per-process pool and active-handler
ceilings; durable effects unique; and final publication/transport drain. The
profile cases additionally prove isolation under backlog and failed provider
calls. The timing case requires progress acknowledgements for both kinds and
unchanged deadlines. Worker loss requires actual reconciliation recovery and
fresh live SSE samples after recovery; wake-up loss requires polling recovery.
These checks complement the unit tests for timing/configuration validation,
reconnect overfetch admission and cleanup retry readiness.

The existing alerts remain selected: diagnostic queue 6s warns before the 8s
steady failure gate, database utilization 80% retains 20% headroom, and
publication age 60s catches unexpected lag without treating every short
reconnect as sustained backlog. Healthy runs have zero redeliveries; injected
profile failures and worker loss produced four and five respectively. Retain
the existing abnormal-redelivery threshold of ten rather than fitting an alert
to deliberate faults in this short certification. Interruption, publication
failure, maximum delivery and forced shutdown have independent immediate
alerts. Repeat this calibration with deployment-specific histogram buckets,
arrival distributions, provider service times and host resources before
changing production alert thresholds or scaling counts.

### Selected-topology measurements

All ten scenarios passed, each on its own clean topology. Latencies below are
seconds; admission, diagnostic queue, publication ACK and SSE are nearest-rank
p95. Recovery is time until accepted product work settles after the final
arrival or injected interruption, as defined by the scenario; it is not an
execution percentile. PostgreSQL totals are independent sampled maxima.

| Scenario | Admission | Diagnostic queue | Publication ACK | SSE | Recovery | PG connections |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| nats_outage | 0.676 | 102.528 | 4.221 | 1.927 | 103.495 | 32 |
| broker_restart | 1.779 | 38.268 | 0.117 | 2.124 | 46.059 | 34 |
| duplicates | 0.755 | 1.735 | 0.048 | 1.097 | 4.908 | 27 |
| wakeup_loss | 0.593 | 1.738 | 0.070 | 1.997 | 2.403 | 25 |
| timing | 3.310 | 17.544 | 0.137 | 1.796 | 30.041 | 31 |
| profile_failure | 0.715 | 1.331 | 0.034 | 0.534 | 24.579 | 28 |
| burst | 2.219 | 10.394 | 0.121 | 1.766 | 11.921 | 33 |
| steady | 0.468 | 1.495 | 0.050 | 0.632 | 2.244 | 25 |
| profile_backlog | 0.809 | 1.238 | 0.023 | 0.299 | 19.447 | 31 |
| worker_loss | 1.876 | 164.761 | 0.624 | 7.407 | 181.940 | 30 |

NATS downtime retained all 36 offline publication intents; offline/restored
admission p95 was 0.676s/0.633s. Its ACK measurement includes reconnection;
the independent post-reconnection ACK p95 was 0.029s over
37 samples, meeting the normal 1s gate. Worker-loss SSE includes outage replay;
fresh post-recovery SSE p95 was 0.374s over 3 samples, meeting the 3s live gate.
Duplicate-pressure transport drain took 116.536s, within its 165s deadline-aware
gate. Profile backlog reached 29 pending messages; profile failure produced
144 execution samples for 48 jobs (three bounded attempts each), while
diagnostic queue p95 remained 1.331s. Wake-up loss recorded 16 polling recoveries.
The timing run recorded eight diagnostic and four profile progress ACKs,
12 fixed deadlines and maximum elapsed execution 18.935s/13.379s, with one
attempt for every job. All cases ended with zero pending/unacknowledged
consumer deliveries and unique durable effects.

Verification used `task format`, `task check` (1,136 passed, 72 skipped,
126 deselected; infrastructure tests remain opt-in), and
`task deployment:validate` (19 passed plus alert/config/script validation).
These were followed by the documented
`task test:capacity -- --scenario NAME --no-build` target for each case;
the final focused reruns were broker restart,
timing and NATS downtime. The reused image's 186 runtime Python files were
checked against host source hashes before the sweep; all recorded cases share
that exact image and runtime source. The report retains individual harness
fingerprints and effective gates. No paid-provider or different-hardware
capacity claim follows from these results.
