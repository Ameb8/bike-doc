# Bike Doc API

FastAPI backend scaffold for Bike Doc.

The backend contract is defined in `../../docs/specs/openapi.yaml`. This
package is set up for a schema-first workflow with `fastapi-code-generator`, but
generated server code has not been created yet.

## Setup

Local Docker Compose configuration is documented in the repository root
`.env.example`. Copy it to a root `.env` before running Compose:

```bash
cp ../../.env.example ../../.env
```

```bash
cd apps/api
uv sync --group dev --group codegen
```

## Run The Empty App

```bash
uv run uvicorn bike_doc_api.main:app --reload
```

The scaffold registers an empty `/v1` API router. Product endpoints should be
added only when their behavior is implemented or generated from the OpenAPI
contract.

## Code Generation

Do not run code generation as part of the initial scaffold. When the API
implementation is ready to be generated from the canonical OpenAPI contract,
use:

```bash
uv run --group codegen fastapi-codegen \
  --input ../../docs/specs/openapi.yaml \
  --output src/bike_doc_api/generated \
  --generate-routers \
  --output-model-type pydantic_v2.BaseModel \
  --python-version 3.12 \
  --use-annotated \
  --strict-nullable \
  --disable-timestamp
```

The generated package is intentionally ignored by git until the team chooses
the exact generation workflow and review process.

## Layout

- `src/bike_doc_api/api`: HTTP routing and request/response adaptation.
- `src/bike_doc_api/schemas`: Pydantic API models aligned with OpenAPI.
- `src/bike_doc_api/services`: product behavior and workflow rules.
- `src/bike_doc_api/repositories`: persistence operations.
- `src/bike_doc_api/models`: SQLAlchemy persistence models.
- `src/bike_doc_api/db`: database session and migration wiring.
- `src/bike_doc_api/adk`: internal Google ADK integration boundary.
- `src/bike_doc_api/providers`: external provider boundaries.

Keep ADK internals behind `src/bike_doc_api/adk`; the Android app talks only to
the product API contract.

## API-hosted durable job maintenance

Run migrations before starting the API (`uv run alembic upgrade head` from
`apps/api` with the configured database URL). The root Compose environment passes
the `BIKE_DOC_API_JOB_*` variables documented in `.env.example` to each API
replica. `BIKE_DOC_API_JOB_MAINTENANCE_ENABLED=true` starts the reusable publisher,
reconciler, and bounded cleanup from FastAPI lifespan. The finite operator
reconstruction command is documented in [QUEUE_RECOVERY.md](QUEUE_RECOVERY.md).
Producer modes default to `legacy`. Enable them only after compatible workers
are deployed. `create_job_maintenance(settings)` registers both diagnostic and
profile recovery policies; an explicitly supplied empty policy registry skips
reconciliation while publishing committed intent.

NATS can be temporarily unavailable at API startup or acceptance. Maintenance
connects lazily, retains unconfirmed generations in PostgreSQL, and retries with
bounded exponential equal jitter. Keep the claim duration longer than the total
publish timeout, and the no-progress threshold longer than claim + polling,
maximum backoff, and reconciliation cadence. Defaults are 16 publications per
batch, 100 reconciliation candidates, 1-second publication polling, a 10-second
publish bound, 30-second leases, 2..60-second retry ceilings, a 300-second
no-progress threshold, and a 30-second reconciliation cadence. A 10-second
shutdown budget cancels loops and closes broker resources; unfinished claims
expire for another host. Multiple API replicas use PostgreSQL coordination.

From the repository root, run:

```bash
task format
task check
task test:maintenance
```

`test:maintenance` needs a working Docker daemon and permission to run local
containers. It creates disposable PostgreSQL 16 and pinned NATS 2.12.1 servers
on private random localhost ports, migrates the fresh database, runs real
maintenance failure/race tests, and removes the containers and volumes on exit.
No production credentials or model provider are required. To use an existing
**disposable migrated** test installation instead:

```bash
cd apps/api
BIKE_DOC_API_MAINTENANCE_TEST_DATABASE_URL=<postgresql+asyncpg-test-url> \
BIKE_DOC_API_MAINTENANCE_TEST_NATS_URL=<test-nats-url> \
uv run pytest -vv -m nats tests/integration/test_job_maintenance.py
```

The tests inspect job rows and broker message state separately. They exercise
cancellation after publish acknowledgement, lease expiry/replay, generation
coalescing, independent replica sessions, and attempt-free reconciliation.
Retention cleanup, broker reconstruction, worker settlement, and diagnostic
effect recovery are owned by their respective follow-up tasks.

Shared worker runtime verification: run `task test:worker` from the repository
root. It creates disposable PostgreSQL and pinned JetStream containers, applies
migrations, and verifies typed fake handlers against real durable job rows and
the profile pull consumer. See `ARCHITECTURE.md` for worker composition, timing,
settlement, and shutdown contracts. Workload handlers and executable role
resource composition are supplied by their feature tasks.

## Diagnostic queue canary

`BIKE_DOC_API_DIAGNOSTIC_EXECUTION=durable_queue` selects external execution for
newly accepted diagnostic turns. It also selects durable profile inference for
eligible images regardless of `BIKE_DOC_API_PROFILE_INFERENCE_EXECUTION`.
Acceptance commits the turn, started event, repair state, null-bound new phase
reference, diagnostic job, optional profile job, and each publication generation
in one PostgreSQL transaction. ADK and NATS are absent from acceptance. The job
inputs remain internal; `202` and persisted SSE contracts are unchanged.

Exactly one diagnostic executor is selected per accepted turn. A committed
`diagnostic_turn` job means external execution; absence means legacy execution.
Replaying an HTTP client key never enqueues or schedules work under a newer
setting. Do not invoke legacy background functions for an existing queued turn.
The profile consumer never consumes diagnostic notifications, and neither job's
retry, acknowledgement, failure, or backlog controls the other's eligibility.
Diagnostic context reads same-turn inferred facts only after their commit.

From the repository root:

```bash
task format
task check
task test:diagnostic-canary
```

The canary needs Docker and uses disposable PostgreSQL 16, JetStream on pinned
NATS 2.12.1, actual migrated ADK storage, and fake models/providers. No cloud
credentials are needed. The script destroys its containers and volumes on exit.
It composes the acceptance, diagnostic, profile, maintenance, shared-runtime,
durable-session, and cross-process SSE suites. Deterministic failure points
cover acceptance rollback, create-before-bind, stale token/deadline, competing
boundary crossing, flushed product writes, durable completion before outcome,
and outcome before acknowledgement. It also exercises broker stop/start,
publisher acknowledgement-window loss, worker process replacement, delayed NAK,
exhaustion, interruption reconciliation, and independent worker completion
orders. Inspect PostgreSQL job/product outcomes separately from JetStream
pending/acknowledgement state; broker state cannot authorize replay.

The task's acceptance evidence is grouped below. Test paths are under
`tests/integration/` unless stated otherwise; the canary command runs them with
real infrastructure rather than relying on the standard suite's skips.

| Contract | Verification |
| --- | --- |
| Atomic turn/event/state/phase/jobs/intents; null binding; no acceptance ADK/NATS I/O | `test_profile_worker.py`: acceptance rollback (including failure between inserts), broker outage acceptance; `test_deferred_diagnostic_sessions.py`: acceptance without ADK |
| Strict inputs, turn-derived diagnostic identity, pinned profile identity | `tests/unit/services/test_background_jobs.py`, `tests/unit/services/test_turn_service.py`, background-job persistence suite |
| Concurrent idempotency, conflicting keys, text-only work, selection through enablement/rollback | `test_profile_worker.py`: concurrent replay for both modes with and without images; `tests/api/test_turns.py`: selection and legacy scheduling |
| Independent consumers, both completion orders, committed-fact visibility and profile failure | `test_diagnostic_worker.py`: `test_two_jobs_execute_independently_and_read_only_committed_facts` |
| Durable session across processes; create-before-bind crash; missing-bound failure | `test_deferred_diagnostic_sessions.py`, `test_adk_postgres.py`, diagnostic missing-binding test |
| Duplicate boundary, replaced/expired token, all protected write families and timeout | `test_diagnostic_worker.py`: fencing matrix and boundary races; `test_worker_runtime.py`: timeout and duplicate claim |
| Exact retry/exhaustion/handled-error/interruption product outcomes; commit/ack loss | Diagnostic terminalization tests, maintenance tests, shared runtime tests |
| Publisher/broker/worker restart, unchanged live HTTP/SSE, worker-first deployment and rollback/drain | Profile outage/restart test; diagnostic live HTTP/SSE subprocess test at normal, pre-boundary and post-boundary crash points; cross-process event wake-up suite |

### Worker-first enablement

1. Apply migrations, including app and ADK schemas, before starting resources.
2. Keep diagnostic producer selection `legacy`. Deploy compatible diagnostic
   and profile workers and API maintenance with both recovery policies. Verify
   strict v1 registries, durable sessions, topology, and timing policy. Locally:
   `docker compose --env-file .env --profile diagnostic-worker --profile profile-worker up -d`.
3. Set `BIKE_DOC_API_JOB_MAINTENANCE_ENABLED=true` and
   `BIKE_DOC_API_DIAGNOSTIC_EXECUTION=durable_queue`, then recreate API producers.
   Keep old worker definitions until all old jobs and retained deliveries settle.
4. Verify new jobs have independent publication generations, one diagnostic
   attempt, persisted terminal events, and no legacy execution. Run the canary
   before expanding deployment. Profile backlog must leave diagnostic capacity
   available.

### Drain and rollback

1. Return producers to `legacy` for **future** turns. Replayed queued acceptances
   retain their external selection. Preserve all PostgreSQL job and ADK rows.
2. Stop fetching and gracefully drain both workload consumers before any fallback
   decision. Locally stop the worker services with their bounded shutdown budget:
   `docker compose --env-file .env stop diagnostic-worker profile-worker`.
3. Inspect PostgreSQL `state`, `execution_token`, `execution_deadline`,
   `effect_boundary_at`, `effect_boundary_token`, attempt count, and persisted
   `turn.completed` for each in-flight diagnostic turn. An unexpired running
   execution remains its sole owner; do not schedule fallback or replace tokens.
4. After the hard deadline, keep maintenance with compatible policies running.
   Pre-boundary loss retries within its budget; exhaustion becomes `dead` with
   the public error/completion pair. Lost post-boundary work becomes
   `interrupted`, or `succeeded` if its product completion already committed.
   Never automatically rerun ADK after a boundary.
5. Resume compatible consumers to settle preserved queued work and terminal
   deliveries. There is no automatic conversion of queue jobs to legacy tasks.
   Fallback for existing work requires proof that no owner or effects remain
   and an explicitly defined safe procedure; changing the deployment setting
   alone supplies no such proof. Terminal turns retry only through a new user
   turn. Do not edit or delete jobs to manufacture replay eligibility.

The canary exercises graceful/forced shutdown and recovery of running jobs on
both sides of the boundary, plus idempotent replay after producer rollback.
Production inspection tooling, retention, broker reconstruction, full load
certification, and final removal of legacy wiring remain outside this canary.

### Full durable queue certification

`task test:queue-certification` runs format/check and certifies all 38 canonical
failure-matrix items twice against clean real PostgreSQL/JetStream and the tuned
multi-replica capacity/privacy topology. See [QUEUE_CERTIFICATION.md](QUEUE_CERTIFICATION.md)
for the executable coverage manifest, bounded workflow and failure interpretation.
