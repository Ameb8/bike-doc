# Single-host production deployment

Use the standalone `compose.production.yaml` with pinned NATS 2.12.1 and the
shared Python runtime. Development `compose.yaml` remains a local convenience;
it is not a secured production configuration. No standalone maintenance daemon
is deployed. Every API replica hosts publication, reconciliation and cleanup.
Both worker services scale independently and share one durable per workload.
Capacity numbers and provisional alert thresholds here require #156 measurements.

## Secrets and network policy

This supported topology is confined to **one trusted host**. NATS, its HTTP
monitoring interface, PostgreSQL and OTLP receivers have no published ports.
Only API and workers attach to both the internal network and an egress network
for providers. Broker/database attach only to the internal network. The gateway
binds loopback; provide your authenticated TLS reverse proxy for external HTTP.
Host administrators and containers on the private network are trusted. NATS HTTP
monitoring is unauthenticated: allow only trusted operations access via `exec`,
never publish 8222. The `monitor.json` NATS identity has read-only stream/consumer
inspection and advisory access, with no pull, ACK, job or wake-up publishing.

Generation requires `--trusted-single-host`; omitting it fails. Do not stretch
this bridge over hosts, expose broker ports, or use plaintext across an untrusted
network. A multi-host deployment must first add server TLS certificates, trusted
CA/client TLS configuration, private monitoring TLS/auth proxy and firewalling;
use `tls://` client endpoints with certificate verification. The supplied
single-host configuration intentionally cannot opt into that unreviewed mode.

```bash
python3 scripts/prepare-production.py /secure/bikedoc --trusted-single-host
export BIKEDOC_SECRETS_DIR=/secure/bikedoc
export BIKEDOC_SECRET_OWNER_UID="$(id -u)"
export COMPOSE_PROJECT_NAME=bikedoc-production
# Persist desired counts across dependency starts and process replacement.
export BIKEDOC_API_REPLICAS=2
export BIKEDOC_DIAGNOSTIC_REPLICAS=2
export BIKEDOC_PROFILE_REPLICAS=2
# Use this array for every command below, from the repository root.
c=(docker compose -f compose.production.yaml)
```

The generator refuses a nonempty directory, writes 0700/0600 permissions, never
prints secrets, and uses random credentials. Keep the directory outside source
control and the build context. `.deploy-secrets/` is ignored as a local fallback.
Compose secrets are host-backed files, not encrypted storage; protect host disk
and backup access. The Collector runs as the secret files' host owner UID (default
1000); set `BIKEDOC_SECRET_OWNER_UID` to that owner so local Compose bind secrets
remain 0600 without requiring the Collector to run as root. Do not run `set -x`, dump process environments or collect
secret files in incidents. The entrypoint reads only its mounted role JSON before
executing the process; PID 1 supervision and signals use Compose init.

Edit **only the relevant role file**, with a secure editor:

| File | Required contract |
| --- | --- |
| api.json | PostgreSQL, API NATS identity, Firebase project/auth, local artifact volume, producer switches, API-hosted maintenance/timing, OTLP; no model key |
| diagnostic.json | PostgreSQL (including durable ADK schema), diagnostic NATS identity, diagnostic/observation/price provider choices and its own provider key, storage, runtime budgets, OTLP; no Firebase or profile configuration |
| profile.json | PostgreSQL, profile NATS identity, profile provider key/model/policy, storage, shared-runtime budgets, OTLP; no diagnostic, Firebase or ADK configuration |
| migrate.json | PostgreSQL URL only |
| monitor.json | Read-only NATS transport identity (not a deployed worker) |
| nats.conf | Broker users and exact subject permissions, single data directory and finite resource caps |
| otlp-export.yaml | Collector `otlp` exporter endpoint plus backend TLS/auth configuration |

Replace Firebase `REPLACE_ME` in api.json and each worker's placeholder
`GOOGLE_API_KEY` with **separate scoped provider credentials**. Firebase ADC, if
required by your auth environment, must be mounted only into API via a local
secrets override. Vertex/GCS credentials similarly belong only to roles that use
them. The default local artifact volume is mounted by API and both workers.
Configure profile resolver policy per the existing profile rollout runbook;
default shadow is safe until reviewed policies are enabled. Settings use
`BIKE_DOC_API_`; `PROCESS_ROLE` is api, diagnostic or profile. API construction
refuses a worker role; production API auth still requires Firebase.

Client permissions use role-specific `_INBOX.<role>.>` namespaces, so pull replies
cannot be snooped by another workload. They separate API work publication/topology management and SSE
publish/subscribe from each worker's own pull/ACK/consumer inspection. Diagnostic
may publish SSE hints; profile cannot. Workers cannot create consumers or pull,
inspect or ACK the other workload. Worker startup verifies the pre-provisioned
stream and **only its own** durable; incompatible definitions fail startup.
API provisioning can create only the two named V1 durables; it cannot create
arbitrary consumers. API and monitoring inspection is scoped to those durables.
The finite operator reconstruction command uses API permissions, never worker
credentials. Do not grant workers `$JS.API.>` or `bikedoc.work.>` subscriptions.

## Bring-up, migrations, and producer enablement

1. Configure secrets and the Collector's backend (`otlp-export.yaml`, exporter
   key `otlp`, OTLP/gRPC endpoint). Application export remains OTLP/HTTP through
   the Collector. No application scrape endpoint is added. Start dependencies:

   ```bash
   "${c[@]}" config --quiet
   "${c[@]}" up -d --wait db nats collector
   "${c[@]}" run --rm migrate
   ```

   Alembic migrates product and pinned ADK schemas before any process starts.
   Back up before schema upgrades; never let ADK lazily create production tables.
2. Keep ingress closed at the reverse proxy until producer verification is
   complete. The legacy flags are execution selection, not admission control.
   Initially set both API `DIAGNOSTIC_EXECUTION` and
   `PROFILE_INFERENCE_EXECUTION` to `legacy` in api.json. Provision topology
   before starting workers (the API also verifies it on publication):

   ```bash
   "${c[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment reconstruct
   "${c[@]}" up -d --wait diagnostic-worker profile-worker
   "${c[@]}" up -d --wait --scale api=2 --scale diagnostic-worker=2 --scale profile-worker=2 api diagnostic-worker profile-worker gateway
   "${c[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment inspect --transport
   ```

   Check each container's `/ready` using the command below. Only a complete
   compatible registry may join a durable consumer; never split old/new kinds
   among replicas expecting subject delivery to find the right handler.
3. Set both API execution switches to `durable_queue`, recreate APIs, and accept
   a controlled diagnostic turn (with an image for profile coverage). Inspect
   confirmed generations, attempts, outcomes, persisted SSE, publication age and
   worker heartbeats. Each acceptance selects exactly one executor; changing a
   producer flag never migrates existing accepted work.

   ```bash
   "${c[@]}" up -d --wait --no-deps --force-recreate api
   "${c[@]}" exec -T api python -c 'import json,urllib.request; print(json.load(urllib.request.urlopen("http://127.0.0.1:8081/ready")))'
   ```

## Health, supervision, stop-fetch and forced loss

`/live` on container loopback port 8081 returns process availability. `/ready`
returns independent process/database/broker/consumer booleans and maintenance
(API) or worker (worker role). It queries migrated job storage and consumer
metadata, never models/providers. API maintenance readiness requires all loops
alive and a recent successful cycle; broker failure is reported separately.
Worker readiness requires its fetch loop alive, a successful latest fetch and
its own consumer accessible. Denied/disconnected pulls are failures, while a
successful empty fetch is healthy.
A provider outage must be diagnosed through execution outcomes, not a readiness
model request. Check any replica with `exec --index N`.

```bash
"${c[@]}" exec -T --index 1 profile-worker python - <<'PYHEALTH'
import json, urllib.error, urllib.request
try:
    response = urllib.request.urlopen("http://127.0.0.1:8081/ready", timeout=15)
except urllib.error.HTTPError as error:
    response = error
print(response.status, json.load(response))
PYHEALTH
"${c[@]}" stop -t 60 diagnostic-worker profile-worker
```

SIGTERM stops fetching first, bounds in-flight completion (default 30 seconds),
then cancels, drains NATS and closes pools/provider resources. The 60-second
Compose budget includes resource-close grace. Keep runtime timing budgets below
this outer bound or increase the deployment bound in the same reviewed change.
API Uvicorn grace is 35 seconds plus maintenance's bounded close. SSE clients
must reconnect/replay after API replacement.
A graceful Uvicorn SIGTERM can exit with status 143 after logging
`Application shutdown complete.`; forced SIGKILL exits 137. The smoke requires
the completion marker from the current API shutdown, and clean worker exits.
Restart policy is `unless-stopped`. Compose health reports failure but does not
itself restart an unhealthy live process. Operators investigate before restarting
one. Unexpected process exit
is supervised; explicit `compose stop` intentionally suppresses restart.

SIGKILL cannot emit a final heartbeat or settle work. Missing telemetry freshness
must detect it. Leave unsettled deliveries untouched: redelivery resolves
current PostgreSQL state; active executions wait for authoritative deadlines,
then API reconciliation applies the kind/effect policy. Post-effect diagnostic
loss becomes interrupted with required product completion and never replays ADK.
Never ACK/delete messages or clear execution tokens to make health green.

## Compatible rollout and rollback

Deploy additive schema migrations first. Deploy a worker image/registry supporting
**all old and new kinds/input versions on that durable** before enabling any new
producer. Drain old replicas or replace them together when they lack a newly
produced definition; arbitrary rolling mixtures of incomplete registries are
unsafe. Verify readiness/complete registry, then roll API producers. Scale using
`up --scale` as above. The deployment smoke exercises worker replacement before
API replacement against the complete product v1 registry. `task test:worker` also
exercises synthetic v1/v2 registries against real PostgreSQL/NATS: v1-only workers
drain first, two complete upgraded replicas become ready before v2 production,
and both versions remain supported through producer rollback and terminal
redelivery. Product-specific future versions still need their compatibility
matrix before producer enablement.

Set the three `BIKEDOC_*_REPLICAS` variables in the deployment environment to
retain the desired counts during every `up`, dependency start and recreation.
An ad hoc `--scale` overrides one invocation; it does not persist across later
commands. Defaults are two replicas per role. The jointly bounded settings and
certification limits are recorded in [CAPACITY.md](CAPACITY.md). Recalculate
the total PostgreSQL and consumer budgets before adding replicas.

For rollback, first stop ingress/new acceptance at the reverse proxy. Use
inspection (`--filters '{"terminal":false,"limit":200}'`, paginate with `after`)
to enumerate **all** queued/retrying/running kinds, versions and effect boundaries.
Stop-fetch/drain workers before replacing an incompatible registry. Running jobs
must finish or expire/reconcile; do not reset a post-effect diagnostic job.
Keep compatible workers until every queued definition is resolvable. Roll API
back only if it understands stored schema and outcomes; restore no old database
over newer committed work. Producer flags can return to `legacy` for **new**
turns while compatible workers finish previously accepted queue jobs. Before
enabling that fallback, restore the prior API release's required legacy provider
configuration and credentials in the API-only secret and verify its existing
canary: the queue-only API template intentionally contains no model keys. Never
schedule those queued turns through legacy executors. Do not downgrade schema
until retained jobs, ADK sessions and rollback compatibility permit it.

## NATS outage and credentials

On broker failure, HTTP acceptance still commits PostgreSQL intent. SSE falls
back to polling. Readiness distinguishes the failed broker; publication/queue
alerts identify lag. Restore the same broker volume, inspect health and backlog,
and let automatic reconnect/resubscribe plus API publication recover. Do not
reconstruct merely because a connection was unavailable. Useful commands:

```bash
"${c[@]}" stop nats
"${c[@]}" start --wait nats
"${c[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment inspect --transport
```

For credential rotation, securely back up the config, add a second user for
one role with identical narrow permissions in nats.conf and update only that
role's JSON URL. Reload with `exec nats sh -c 'kill -HUP 1'` (NATS is PID 1).
Recreate that role to read its new secret, verify readiness and positive/negative
permissions with the deployment harness, then remove the old user and reload.
Rotate one role at a time; never regenerate the whole directory over a live
PostgreSQL password. Existing connections may need recreation to enforce
revocation; for incident revocation restart NATS and recreate affected roles.
For changes using atomic file replacement, Compose secret bind mounts retain old
inodes: recreate NATS before expecting the new config. A config-only reload is
safe only after verifying the mounted file contains the new configuration.
Never print passwords, URLs, connection dumps or secret JSON to prove rotation.

Incident collection: record image/version, `compose ps`, boolean readiness,
read-only job inspection pages, broker `/healthz` and `/jsz` capacity totals,
contract alert names and bounded application event categories. Access monitoring
with `exec nats wget -q -O - http://127.0.0.1:8222/jsz`. Restrict access/retention
for opaque IDs. Avoid raw broker payloads, provider logs, env dumps, SQL job input,
artifact paths and full third-party exception text. Inspect/redact logs locally
before sharing; do not archive secret-bearing Docker inspect output.

## Backup, disk loss and reconstruction

One broker server owns the Compose-project-scoped `nats_data` volume. A fixed
container name prevents Compose scaling NATS; another project creates a different
volume. Do not replace the project-scoped volume name with `external`, a shared name
or host mount, or manually
start another server on this volume. Never use `down -v` on production. Single
node storage accepts **host-disk loss of transport state**; PostgreSQL plus
artifact backups remain required. If availability goals later require surviving
host loss without reconstruction downtime, deploy a reviewed three-node
JetStream replicated stream with independent disks and TLS. Multiple servers
sharing this data directory are not replication.

For a consistent offline backup: stop ingress, stop API/workers, stop NATS, then
snapshot its volume on the host. Record the exact server version, topology and
retention settings alongside it; protect backups as credentials/operational data.
Back up PostgreSQL with its supported consistent backup tool and the artifact
volume separately. For example (destination must already exist, be private):

```bash
"${c[@]}" stop -t 60 api diagnostic-worker profile-worker
"${c[@]}" stop nats
NATS_VOLUME="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${COMPOSE_PROJECT_NAME}-jetstream")"
docker run --rm -v "$NATS_VOLUME:/source:ro" -v "$BACKUP_DIR:/backup" alpine:3.21 tar -C /source -czf /backup/nats.tgz .
"${c[@]}" start --wait nats
"${c[@]}" up -d --wait api diagnostic-worker profile-worker
```

Restore only with NATS stopped into an empty project volume. Retain the original
volume and select a fresh disk, then restore the offline archive before starting
the broker:

```bash
"${c[@]}" stop -t 60 api diagnostic-worker profile-worker
"${c[@]}" stop nats
export BIKEDOC_NATS_VOLUME_SUFFIX="_restore_$(date +%Y%m%d_%H%M%S)"
RESTORE_VOLUME="${COMPOSE_PROJECT_NAME}_nats_data${BIKEDOC_NATS_VOLUME_SUFFIX}"
docker volume create "$RESTORE_VOLUME"
docker run --rm -v "$RESTORE_VOLUME:/target" -v "$BACKUP_DIR:/backup:ro" alpine:3.21 sh -c \
  'test -z "$(ls -A /target)" && tar -C /target -xzf /backup/nats.tgz'
"${c[@]}" up -d --wait --force-recreate nats
"${c[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment reconstruct
"${c[@]}" up -d --wait diagnostic-worker profile-worker api
```

Preserve ownership and persist the selected suffix for subsequent restarts.
The smoke harness restores onto a fresh volume and verifies a SHA-256 manifest
of the stopped source files before restarting, rather than extracting over an
existing disk. Never restore an old PostgreSQL
snapshot under live workers, or assume transport and database snapshots are
transactionally aligned. Duplicate restored notifications are harmless through
PostgreSQL claim/terminal no-op rules. A stale broker snapshot can omit confirmed
notifications: after verifying PostgreSQL and topology, run reconstruction.
For lost/corrupt broker storage, close ingress and stop application processes.
Select a **new empty volume** with the recovery suffix; the old project volume
remains intact for investigation. Persist this suffix in deployment configuration
after recovery so subsequent restarts keep the recovered disk. Never change it
merely to resolve a connection outage. Run:

```bash
"${c[@]}" stop -t 60 api diagnostic-worker profile-worker
"${c[@]}" stop nats
export BIKEDOC_NATS_VOLUME_SUFFIX="_rebuild_$(date +%Y%m%d_%H%M%S)"
"${c[@]}" up -d --wait --force-recreate nats
"${c[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment reconstruct
"${c[@]}" up -d --wait diagnostic-worker profile-worker api
```

This uses Task 4.1's finite policy-aware command, not a message replay. It skips
terminal, running, future, exhausted, unknown and unsafe diagnostic work; API
maintenance recovers expired executions and publishes eligible intent. See
[QUEUE_RECOVERY.md](../apps/api/QUEUE_RECOVERY.md) for eligibility and retention.

## Alerts and executable verification

`deploy/alerts.json` defines backend-neutral conditions over Task 4.2 OTLP
instruments. Threshold rationale and capacity-test safety margins are in
[CAPACITY.md](CAPACITY.md); these are operational warnings, not production SLOs.
The deployment's OTLP backend must implement each rule's selector and reduction
over a rolling 300-second window and evaluate every 30 seconds. Configure its
notification routing, access and retention there. Run:

```bash
task deployment:validate
task test:deployment
task test:recovery
task format
task check
```

Application names are `bike_doc.queue.<metric>`, attributes exactly as specified
by `core/queue_contract.py`. Backend samples for the executable harness are JSON
objects keyed by alert name: `{"publication_lag":{"value":90,"age_seconds":0}}`.
`value` is the backend's window reduction, not a raw cumulative counter; age is
since the freshest contributing measurement. `increase` handles counter resets
per process before summation. `p95` merges histogram buckets before quantiling;
never average quantiles. `max` drops samples older than 120 seconds before
reducing. Worker availability takes the max across fresh replicas for each
explicit workload; absence fires even after SIGKILL. Publication gauges and
broker observations are duplicated by API replicas: use max, never sum.
`pool_utilization` is max per-process checked_out / (size + overflow), grouped by the unique configured OTLP `service.name` **and pool `role`**;
exclude the SSE alias of the API pool. Roles `maintenance` and `adk` identify
their separate bounded pools.
Deployment appends the opaque container hostname to the configured base service
name, e.g. `bike-doc-profile.<hostname>`. Aggregate by base name/workload across
replicas and preserve the full service name for counter resets and pool ratios.
This uses the canonical three resource fields. The backend must preserve process identity to avoid combining different pools.
Zero denominators are unknown, not healthy. Do not use identities as metric
attributes in application code.

The backend also polls private NATS `/jsz` to supply `nats.storage_utilization`
and `nats.memory_utilization` as used / configured limit;
`nats.resource_alarm` is 1 for a failing JetStream `/healthz` or a broker
resource/storage error and 0 otherwise; these are infrastructure
signals, not new application instrumentation. Missing resource measurements
fire. Missing publication observations detect pipeline loss. Missing latency
samples without accepted work do not indicate degradation. Alert evaluation is
read-only and never drives job transitions. To validate a reduced backend window:

```bash
cd apps/api
uv run python -m bike_doc_api.operations.alerts ../../deploy/alerts.json --samples /secure/reduced-window.json
```

The unit harness crosses and clears every threshold and checks missing/stale
heartbeats and the metric/dimension contract. `test:deployment` uses disposable
secrets/volumes, placeholder provider keys with **no accepted model work**, real
role credentials, migration ordering, two replicas of every role, worker-first
replacement, broker outage/reconnect, forced process restart, finite inspection
and reconstruction, and bounded stop. In-flight safety and terminal/unsafe
reconstruction are exercised by `test:recovery` against real PostgreSQL/NATS with
fake model/providers. Provider correctness/capacity certification remains in
#156/#157; do not mistake this idle topology smoke for provider integration.

See [acceptance evidence](VERIFICATION.md) for the contract-to-test mapping.
