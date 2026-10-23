# Task #155 acceptance evidence

Run from the repository root with Docker available:
`task deployment:validate`, `task test:deployment`, `task test:recovery`,
`task format`, `task check`. All infrastructure targets own isolated projects,
credentials and volumes; never point them at production. The image build uses
the locked dependencies without development tools, one shared image, precompiled
Python bytecode for cold startup and no package-download cache in its layers.

| Acceptance | Implementation and executable evidence |
| --- | --- |
| API maintenance and independent worker scaling over persistent pinned broker | Standalone Compose; persistent per-role replica settings; deployment smoke starts migrated PostgreSQL, NATS 2.12.1 and two replicas each of API/diagnostic/profile, asserts dependency starts retain those counts; role `/ready` checks |
| Narrow environments and isolated workload credentials | Production role secrets plus narrow development anchors; static contract tests and real positive pull/ACK, cross-role pull/inspect/publish/subscribe denial, isolated reply inboxes and SSE permissions |
| Private endpoints, secrets and trusted network/TLS policy | No broker/database/Collector/application ports published; gateway loopback only; 0700/0600 generator; external plaintext endpoint rejection and HTTP role gate unit tests |
| Incompatible definitions refused, no shared concurrent data directory | Real mismatched MaxAge refusal, scoped worker missing-topology tests; fixed broker container name/unique project volume and real rejected `--scale nats=2` |
| Independent health without provider calls | Boolean loopback probes; unit database/broker/consumer failures, maintenance freshness/dead-loop tests, historical connection-error recovery versus authorization denial; real broker/database outages with live process and failed readiness |
| Bounded graceful and forced shutdown | Existing shared runtime stop-fetch/settlement/close; outer Compose budget and supervision; smoke graceful stop with clean exit-code assertions, SIGKILL/restart; recovery suite external process loss, deadline/fencing and terminal redelivery |
| Required versioned alerts | Metric/dimension validation plus every rule's synthetic crossing/recovery; stale/missing availability, resource and telemetry signals; backend window and OTLP mapping in runbook |
| Worker-first rollout and safe rollback | Smoke replaces complete v1 workers before APIs; shared-runtime real PostgreSQL/NATS `test_worker_first_version_rollout_and_producer_rollback` drains v1-only workers, readies two complete v1/v2 registries before new production, and retains both through producer rollback/terminal redelivery; real diagnostic test `test_live_http_sse_with_external_diagnostic_process_and_rollback` preserves acceptance through worker loss/restart and records a fresh durable job after consumer drain |
| Safe backup/reconstruction | Smoke offline archive restored onto a fresh disk with every source file's SHA-256 verified before restart, empty-disk recovery suffix preserving the old volume, and finite containerized reconstruction; Task 4.1 real reconstruction suites preserve running/future/exhausted/terminal/unsafe work, generations and product outcomes |
| Configuration/operational smoke and repository checks | Task targets above; deployment smoke also exercises inspection, reconnect and overlapping credential rotation/reload/revocation and incident-log secret exclusion |

There is no new maintenance deployment, second worker runtime, provider call in
readiness, public API endpoint, instrumentation exporter or canonical-spec edit.
Final capacity, certification and queue-only gates are documented in
[QUEUE_CUTOVER.md](../apps/api/QUEUE_CUTOVER.md). Alert routing and backend-specific OTLP TLS credentials are supplied
by the operator; synthetic contract validation does not claim vendor delivery.

Verified on 2026-10-19 with disposable Docker infrastructure:

| Command | Result |
| --- | --- |
| `task format` | Passed |
| `task check` | 1,105 passed, 72 skipped, 126 deselected; formatting, lint and types passed |
| `task deployment:validate` | 19 passed; shell/configuration and synthetic alert validation passed |
| `task test:worker` | 20 real PostgreSQL/JetStream integration cases passed |
| `task test:recovery` | 102 integration cases and 48 database persistence checks passed |
| `task test:deployment` | Passed the complete secured, scaled deployment lifecycle, including shutdown assertions and cleanup |

The smoke exposed and now guards against transient `--scale` settings being
lost on later dependency starts and historical NATS connection errors keeping
reconnected workers unhealthy. Supervision reads the complete container list
before selecting a replica. Shutdown checks accept Uvicorn's graceful SIGTERM
status 143 only with a new lifespan-completion log; workers must exit zero.
Live provider calls and vendor notification delivery require operator credentials
and backend configuration and were not exercised by these deployment checks.
