#!/usr/bin/env bash
set -euo pipefail
trap 'printf "Deployment smoke failed at line %s (status %s)\n" "$LINENO" "$?" >&2' ERR
cd "$(dirname "$0")/.."
secrets_dir="$(mktemp -d)"
export BIKEDOC_SECRETS_DIR="$secrets_dir"
export BIKEDOC_SECRET_OWNER_UID="$(id -u)"
export COMPOSE_PROJECT_NAME="bikedoc-smoke-$(date +%s)-$$"
export BIKEDOC_HTTP_PORT=0
export BIKEDOC_API_REPLICAS=2
export BIKEDOC_DIAGNOSTIC_REPLICAS=2
export BIKEDOC_PROFILE_REPLICAS=2
compose=(docker compose -f compose.production.yaml)
cleanup() {
  "${compose[@]}" down -v --remove-orphans >/dev/null
  docker volume rm "${COMPOSE_PROJECT_NAME}_nats_data" "${COMPOSE_PROJECT_NAME}_nats_data_restored" >/dev/null 2>&1 || true
  rm -rf "$secrets_dir"
}
trap cleanup EXIT
python3 scripts/prepare-production.py "$secrets_dir" --trusted-single-host
# Idle workers use syntactically valid placeholder keys; no job invokes a provider.
"${compose[@]}" config --quiet
"${compose[@]}" build migrate
"${compose[@]}" up -d --wait db nats
if "${compose[@]}" up -d --scale nats=2 nats; then
  echo "Broker scaling unexpectedly allowed" >&2
  exit 1
fi
"${compose[@]}" run --rm migrate
"${compose[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment reconstruct
"${compose[@]}" run --rm --no-deps -v "$secrets_dir:/verification:ro" \
  -v "$PWD/deploy/verify_permissions.py:/verify.py:ro" --entrypoint python operator /verify.py
# Bring complete worker roles up before API producers. Stage cold imports on
# small verification hosts while still keeping two live replicas of every role.
"${compose[@]}" up -d --wait --scale diagnostic-worker=2 diagnostic-worker
"${compose[@]}" up -d --wait --scale profile-worker=2 profile-worker
"${compose[@]}" up -d --wait --scale api=2 api
probe() {
  "${compose[@]}" exec -T --index 1 "$1" python -c \
    'import json,urllib.request; print(json.load(urllib.request.urlopen("http://127.0.0.1:8081/ready")))'
}
"${compose[@]}" run --rm --no-deps collector validate --config=/etc/otelcol/config.yaml
"${compose[@]}" up -d collector gateway
for service in api diagnostic-worker profile-worker; do
  test "$("${compose[@]}" ps -q "$service" | wc -l)" -eq 2
done
probe api
probe diagnostic-worker
probe profile-worker
# Same complete registry rolled worker-first, then producers/API.
"${compose[@]}" up -d --wait --force-recreate --no-deps diagnostic-worker profile-worker
"${compose[@]}" up -d --wait --force-recreate --no-deps api
"${compose[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment inspect --transport
"${compose[@]}" stop nats
# /live stays available; /ready identifies broker/consumer loss.
"${compose[@]}" exec -T api python -c '
import json,urllib.request,urllib.error
assert json.load(urllib.request.urlopen("http://127.0.0.1:8081/live"))["process"]
try: urllib.request.urlopen("http://127.0.0.1:8081/ready")
except urllib.error.HTTPError as error:
    result=json.load(error); assert result["database"] and not result["broker"] and not result["consumer"], result
else: raise AssertionError("broker outage did not fail readiness")'
"${compose[@]}" start --wait nats
"${compose[@]}" up -d --wait api diagnostic-worker profile-worker
# A database outage is independently visible while broker/consumer remain healthy.
"${compose[@]}" stop db
"${compose[@]}" exec -T profile-worker python -c '
import json,urllib.request,urllib.error
try: urllib.request.urlopen("http://127.0.0.1:8081/ready")
except urllib.error.HTTPError as error:
    result=json.load(error); assert not result["database"] and result["broker"] and result["consumer"], result
else: raise AssertionError("database outage did not fail readiness")'
"${compose[@]}" start --wait db
"${compose[@]}" up -d --wait api diagnostic-worker profile-worker
# Kill the supervised child, rather than `compose kill` (an operator stop).
# Require automatic restart before `up --wait` checks readiness.
for service in diagnostic-worker api; do
  container_ids="$("${compose[@]}" ps -q "$service")"
  container_id="${container_ids%%$'\n'*}"
  test -n "$container_id"
  before="$(docker inspect --format '{{.RestartCount}}' "$container_id")"
  docker exec "$container_id" python -c '
import os,signal
from pathlib import Path
for child in Path("/proc/1/task/1/children").read_text().split():
    os.kill(int(child), signal.SIGKILL)' || true
  restarted=false
  for _ in $(seq 1 60); do
    after="$(docker inspect --format '{{.RestartCount}}' "$container_id")"
    if (( after > before )); then
      restarted=true
      break
    fi
    sleep 1
  done
  if [[ "$restarted" != true ]]; then
    echo "Supervisor did not restart $service after process death" >&2
    exit 1
  fi
done
"${compose[@]}" up -d --wait diagnostic-worker api
"${compose[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment reconstruct
# Rotate API credentials with overlap, reload in place, then recreate its clients.
python3 - "$secrets_dir" <<'ROTATE'
import json,secrets,sys
from pathlib import Path
root=Path(sys.argv[1])
path=root / "nats.conf"
config=json.loads(path.read_text())
old=next(user for user in config["authorization"]["users"] if user["user"]=="api")
replacement=dict(old,user="api_next",password=secrets.token_hex(32))
config["authorization"]["users"].append(replacement)
path.write_text(json.dumps(config))
role=root / "api.json"
values=json.loads(role.read_text())
retired=root / "retired-api-url"
retired.write_text(values["BIKE_DOC_API_NATS_URL"])
retired.chmod(0o600)
values["BIKE_DOC_API_NATS_URL"]=f"nats://api_next:{replacement['password']}@nats:4222"
role.write_text(json.dumps(values))
ROTATE
"${compose[@]}" exec -T nats sh -c 'kill -HUP 1'
"${compose[@]}" up -d --wait --force-recreate --no-deps api
probe api
python3 - "$secrets_dir" <<'REVOKE'
import json,sys
from pathlib import Path
path=Path(sys.argv[1]) / "nats.conf"
config=json.loads(path.read_text())
config["authorization"]["users"]=[user for user in config["authorization"]["users"] if user["user"]!="api"]
path.write_text(json.dumps(config))
REVOKE
"${compose[@]}" exec -T nats sh -c 'kill -HUP 1'
"${compose[@]}" run --rm --no-deps -v "$secrets_dir:/verification:ro" \
  -v "$PWD/deploy/verify_rotation.py:/verify.py:ro" --entrypoint python operator /verify.py
"${compose[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment inspect --transport
# Offline volume backup/restore: no concurrent writer; never delete another project.
"${compose[@]}" stop -t 60 diagnostic-worker profile-worker api
"${compose[@]}" stop nats
docker run --rm --entrypoint sh -v "${COMPOSE_PROJECT_NAME}_nats_data:/data:ro" \
  -v "$secrets_dir:/backup" nats:2.12.1-alpine -c \
  'cd /data && find . -type f -exec sha256sum {} \; | sort > /backup/nats.sha256 && test -s /backup/nats.sha256 && tar -czf /backup/nats.tgz .'
# Restore onto an empty disk, prove every file matches, then start that disk.
export BIKEDOC_NATS_VOLUME_SUFFIX=_restored
docker volume create "${COMPOSE_PROJECT_NAME}_nats_data_restored" >/dev/null
docker run --rm --entrypoint sh -v "${COMPOSE_PROJECT_NAME}_nats_data_restored:/data" \
  -v "$secrets_dir:/backup:ro" nats:2.12.1-alpine -c \
  'test -z "$(ls -A /data)" && tar -C /data -xzf /backup/nats.tgz && cd /data && sha256sum -c /backup/nats.sha256'
"${compose[@]}" up -d --wait --force-recreate nats
"${compose[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment reconstruct
"${compose[@]}" up -d --wait api diagnostic-worker profile-worker
probe api
# Reconstruct onto a new empty broker disk, retaining the old snapshot volume.
"${compose[@]}" stop -t 60 diagnostic-worker profile-worker api
"${compose[@]}" stop nats
export BIKEDOC_NATS_VOLUME_SUFFIX=_reconstructed
"${compose[@]}" up -d --wait --force-recreate nats
docker volume inspect "${COMPOSE_PROJECT_NAME}_nats_data" >/dev/null
"${compose[@]}" run --rm --no-deps operator python -m bike_doc_api.operations.deployment reconstruct
"${compose[@]}" up -d --wait api diagnostic-worker profile-worker
probe api
# Collect a bounded incident bundle and check that deployment secrets are absent.
"${compose[@]}" logs --no-color --since 10m api diagnostic-worker profile-worker nats > "$secrets_dir/incident.log"
python3 - "$secrets_dir" <<'PRIVACY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
path=root / "incident.log"
path.chmod(0o600)
logs=path.read_text()
config=json.loads((root / "nats.conf").read_text())
assert all(user["password"] not in logs for user in config["authorization"]["users"])
assert (root / "postgres_password").read_text() not in logs
assert (root / "retired-api-url").read_text() not in logs
for role in ("api", "diagnostic", "profile"):
    values=json.loads((root / f"{role}.json").read_text())
    assert values["BIKE_DOC_API_NATS_URL"] not in logs
    assert values["BIKE_DOC_API_DATABASE_URL"] not in logs
PRIVACY
shutdown_started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"${compose[@]}" stop -t 60 diagnostic-worker profile-worker api
for service in api diagnostic-worker profile-worker; do
  for container_id in $("${compose[@]}" ps -a -q "$service"); do
    exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$container_id")"
    # Uvicorn re-raises SIGTERM after completing graceful shutdown. Require its
    # positive completion marker from this stop, as well as the expected status.
    if [[ "$service" == api && ( "$exit_code" == 0 || "$exit_code" == 143 ) ]]; then
      shutdown_logs="$(docker logs --since "$shutdown_started" "$container_id" 2>&1)"
      if [[ "$shutdown_logs" != *'Application shutdown complete.'* ]]; then
        echo 'API did not complete graceful lifespan shutdown' >&2
        exit 1
      fi
    elif [[ "$exit_code" != 0 ]]; then
      echo "$service exited with $exit_code during graceful shutdown" >&2
      exit 1
    fi
  done
done
printf '%s\n' 'Production topology smoke passed; use task test:recovery for in-flight safety injection.'
