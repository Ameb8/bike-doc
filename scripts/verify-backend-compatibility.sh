#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
certification_output=""
if [[ "${1:-}" == certification ]]; then
  certification_output="$2"
fi
setup_phase=initialization
phase() {
  setup_phase="$1"
  if [[ -n "$certification_output" ]]; then
    printf '{"phase":"%s","status":"%s"}\n' "$1" "$2" \
      > "$certification_output/setup.json"
  fi
}
phase initialization running
suffix="$(python3 -c 'import uuid; print(uuid.uuid4().hex[:16])')"
db_name="bike-doc-compat-db-${suffix}"
nats_name="bike-doc-compat-nats-${suffix}"
db_volume="${db_name}-data"
nats_volume="${nats_name}-data"
password="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"

cleanup() {
  local outcome=$?
  if [[ "$outcome" != 0 ]]; then
    phase "$setup_phase" failed
  fi
  docker rm -f "$db_name" "$nats_name" >/dev/null 2>&1 || true
  docker volume rm "$db_volume" "$nats_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 143' TERM INT

phase database_start running
docker volume create "$db_volume" >/dev/null
docker volume create "$nats_volume" >/dev/null
docker run -d --name "$db_name" \
  -e POSTGRES_USER=bikedoc -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=bikedoc \
  -p 127.0.0.1::5432 -v "$db_volume:/var/lib/postgresql/data" \
  postgres:16-alpine >/dev/null
phase broker_start running
# A fixed selected host port survives Docker stop/start failure injection.
nats_host_port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
docker run -d --name "$nats_name" -p "127.0.0.1:${nats_host_port}:4222" \
  -v "$nats_volume:/data" nats:2.12.1-alpine \
  -js -sd /data -m 8222 >/dev/null

phase readiness running
ready=false
for _ in $(seq 1 60); do
  if docker exec "$db_name" pg_isready -q -U bikedoc -d bikedoc \
    && docker exec "$nats_name" wget -q -O /dev/null \
      'http://127.0.0.1:8222/healthz?js-enabled-only=true'; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  echo 'Disposable PostgreSQL or NATS did not become ready within 60 seconds' >&2
  exit 1
fi

db_mapping="$(docker port "$db_name" 5432/tcp)"
nats_mapping="$(docker port "$nats_name" 4222/tcp)"
db_port="${db_mapping##*:}"
nats_port="${nats_mapping##*:}"
export BIKE_DOC_API_DATABASE_URL="postgresql+asyncpg://bikedoc:${password}@127.0.0.1:${db_port}/bikedoc"
export BIKE_DOC_API_ADK_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL"
export BIKE_DOC_API_EVENT_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL"
export BIKE_DOC_API_EVENT_TEST_NATS_URL="nats://127.0.0.1:${nats_port}"

export BIKE_DOC_API_MAINTENANCE_TEST_NATS_CONTAINER="$nats_name"
export BIKE_DOC_API_MAINTENANCE_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL"
export BIKE_DOC_API_MAINTENANCE_TEST_NATS_URL="$BIKE_DOC_API_EVENT_TEST_NATS_URL"

cd apps/api
phase migrations running
uv run alembic upgrade head
if [[ "${1:-}" == certification ]]; then
  export BIKE_DOC_API_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL"
  phase tests running
  uv run python tests/certification/run_tests.py "$2"
  phase completed passed
  exit 0
fi
if [[ "${1:-}" == queue-cutover ]]; then
  uv run pytest -vv -m nats \
    tests/integration/test_profile_worker.py::test_acceptance_outage_ack_window_broker_and_worker_restart \
    tests/integration/test_diagnostic_worker.py::test_two_jobs_execute_independently_and_read_only_committed_facts \
    tests/integration/test_diagnostic_worker.py::test_live_http_sse_with_external_diagnostic_process_and_rollback \
    tests/integration/test_diagnostic_worker.py::test_cutover_accounts_for_historical_inflight_without_backfilling_jobs \
    tests/integration/test_job_maintenance.py::test_reconstruction_broker_loss_restart_overlap_and_delivery \
    tests/integration/test_worker_runtime.py::test_observability_real_transport_execution_recovery_and_privacy \
    tests/integration/test_worker_runtime.py::test_shutdown_drain_and_forced_loss_redelivery \
    tests/integration/test_worker_runtime.py::test_worker_first_version_rollout_and_producer_rollback \
    tests/integration/test_cross_process_event_wakeups.py::test_cross_process_event_delivery
  exit 0
fi
if [[ "${1:-}" == observability ]]; then
  uv run pytest -vv -m nats tests/integration/test_worker_runtime.py tests/integration/test_job_maintenance.py tests/integration/test_diagnostic_worker.py tests/integration/test_cross_process_event_wakeups.py
  exit 0
fi
if [[ "${1:-}" == recovery ]]; then
  uv run pytest -vv -m nats tests/integration/test_job_maintenance.py tests/integration/test_diagnostic_worker.py tests/integration/test_profile_worker.py tests/integration/test_nats_compatibility.py
  BIKE_DOC_API_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL" \
    uv run pytest tests/unit/repositories/test_background_job_persistence.py
  exit 0
fi
if [[ "${1:-}" == diagnostic-canary ]]; then
  # Downgrade checks require a clean database. Run them before failure tests
  # intentionally retain accepted jobs and deferred phase references.
  uv run pytest -vv tests/integration/test_deferred_diagnostic_sessions.py tests/integration/test_adk_postgres.py
  uv run pytest -vv -m nats tests/integration/test_job_maintenance.py tests/integration/test_worker_runtime.py tests/integration/test_profile_worker.py tests/integration/test_diagnostic_worker.py tests/integration/test_cross_process_event_wakeups.py
  BIKE_DOC_API_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL" \
    uv run pytest tests/unit/repositories/test_background_job_persistence.py
  exit 0
fi
if [[ "${1:-}" == diagnostic ]]; then
  uv run pytest -vv -m nats tests/integration/test_diagnostic_worker.py
  exit 0
fi
if [[ "${1:-}" == sessions ]]; then
  uv run pytest -vv tests/integration/test_deferred_diagnostic_sessions.py tests/integration/test_adk_postgres.py
  BIKE_DOC_API_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL" \
    uv run pytest tests/unit/repositories/test_diagnostic_persistence.py
  exit 0
fi
if [[ "${1:-}" == canary ]]; then
  uv run pytest -vv -m nats tests/integration/test_job_maintenance.py tests/integration/test_worker_runtime.py tests/integration/test_profile_worker.py
  BIKE_DOC_API_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL" \
    uv run pytest tests/unit/repositories/test_background_job_persistence.py
  exit 0
fi
if [[ "${1:-}" == profile ]]; then
  uv run pytest -vv -m nats tests/integration/test_profile_worker.py
  exit 0
fi
if [[ "${1:-}" == worker ]]; then
  uv run pytest -vv -m nats tests/integration/test_worker_runtime.py
  exit 0
fi
if [[ "${1:-}" == maintenance ]]; then
  uv run pytest -vv -m nats tests/integration/test_job_maintenance.py
  BIKE_DOC_API_TEST_DATABASE_URL="$BIKE_DOC_API_DATABASE_URL" \
    uv run pytest tests/unit/repositories/test_background_job_persistence.py
  exit 0
fi
uv run pytest -vv tests/integration/test_deferred_diagnostic_sessions.py tests/integration/test_adk_postgres.py
uv run pytest -vv -m nats tests/integration/test_nats_compatibility.py
for _ in $(seq 1 "${SSE_REPEATS:-1}"); do
  uv run pytest -vv -m nats tests/integration/test_cross_process_event_wakeups.py
done
