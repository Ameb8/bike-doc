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
All newly accepted work records durable jobs. Open ingress only after compatible workers
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

## Queue-only execution

Every new diagnostic turn records a durable diagnostic job; eligible images also
record an independent profile job. Acceptance, immutable input, initial event
and publication intent commit atomically without NATS or ADK availability.
Idempotent replays return the original acceptance without recording work, even
for historical rows without jobs. Public `202` and SSE contracts are unchanged.

Both worker roles use the shared runtime and independent consumers. Diagnostic
execution reads only committed profile facts and cannot be blocked by profile
backlog. Durable ADK state is initialized before the fenced effect boundary.

Use [QUEUE_CUTOVER.md](QUEUE_CUTOVER.md) for the prerequisite certificate,
worker-first rollout, historical drain/accounting, smoke tests and rollback.
The standard verification is `task format`, `task check`,
`task test:queue-certification`, and `task test:queue-cutover` from the root.

### Full durable queue certification

`task test:queue-certification` runs format/check and certifies all 38 canonical
failure-matrix items twice against clean real PostgreSQL/JetStream and the tuned
multi-replica capacity/privacy topology. See [QUEUE_CERTIFICATION.md](QUEUE_CERTIFICATION.md)
for the executable coverage manifest, bounded workflow and failure interpretation.
