#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
suffix="$(python3 -c 'import uuid; print(uuid.uuid4().hex[:16])')"
db_name="bike-doc-compat-db-${suffix}"
nats_name="bike-doc-compat-nats-${suffix}"
db_volume="${db_name}-data"
nats_volume="${nats_name}-data"
password="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"

cleanup() {
  docker rm -f "$db_name" "$nats_name" >/dev/null 2>&1 || true
  docker volume rm "$db_volume" "$nats_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker volume create "$db_volume" >/dev/null
docker volume create "$nats_volume" >/dev/null
docker run -d --name "$db_name" \
  -e POSTGRES_USER=bikedoc -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=bikedoc \
  -p 127.0.0.1::5432 -v "$db_volume:/var/lib/postgresql/data" \
  postgres:16-alpine >/dev/null
docker run -d --name "$nats_name" -p 127.0.0.1::4222 \
  -v "$nats_volume:/data" nats:2.12.1-alpine \
  -js -sd /data -m 8222 >/dev/null

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

cd apps/api
uv run alembic upgrade head
uv run pytest -vv tests/integration/test_adk_postgres.py
uv run pytest -vv -m nats tests/integration/test_nats_compatibility.py
for _ in $(seq 1 "${SSE_REPEATS:-1}"); do
  uv run pytest -vv -m nats tests/integration/test_cross_process_event_wakeups.py
done
