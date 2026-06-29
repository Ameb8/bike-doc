#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
TOKEN="${TOKEN:-dev-token}"
BIKE_ID="${BIKE_ID:-bike_01J6N7P8Q9R4S5T6U7V8W9X0YZ}"
IMAGE="${IMAGE:-}"
MIME="${MIME:-image/jpeg}"
TEXT="${TEXT:-The chain skips when I pedal hard in the middle gears.}"
CLIENT_SESSION_ID="${CLIENT_SESSION_ID:-demo-$(date +%s)}"
STREAM_TIMEOUT_SECONDS="${STREAM_TIMEOUT_SECONDS:-30}"
REPLAY_AFTER="${REPLAY_AFTER:-returned}"
MAX_STREAM_ROUNDS="${MAX_STREAM_ROUNDS:-3}"

SESSION_ID=""
LAST_EVENT_ID="0"
CURRENT_INPUT_REQUEST_ID=""
CURRENT_STATUS=""
CURRENT_DIAGNOSTIC_REPORT_ID=""
LAST_PRINTED_REPORT_ID=""
UPLOADED_ARTIFACT_IDS_JSON='[]'
NEXT_STREAM_PATH=""

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

print_section() {
  printf '\n== %s ==\n' "$1"
}

api_get() {
  local url="$1"

  curl --fail-with-body -sS "$url" \
    -H "Authorization: Bearer $TOKEN"
}

api_post_json() {
  local url="$1"
  local payload="$2"

  curl --fail-with-body -sS -X POST "$url" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload"
}

upload_artifact() {
  local image_path="$1"
  local client_artifact_id="$2"
  local artifact_response
  local artifact_id

  if [[ -z "$image_path" ]]; then
    UPLOADED_ARTIFACT_IDS_JSON='[]'
    return
  fi

  if [[ ! -f "$image_path" ]]; then
    echo "IMAGE path does not exist: $image_path" >&2
    exit 1
  fi

  print_section "Upload Artifact"
  artifact_response="$(
    curl --fail-with-body -sS -X POST "$BASE_URL/v1/artifacts" \
      -H "Authorization: Bearer $TOKEN" \
      -F "file=@$image_path;type=$MIME" \
      -F "purpose=diagnostic_photo" \
      -F "repair_session_id=$SESSION_ID" \
      -F "client_artifact_id=$client_artifact_id"
  )"
  echo "$artifact_response" | jq

  artifact_id="$(jq -r '.artifact.id' <<<"$artifact_response")"
  if [[ -z "$artifact_id" || "$artifact_id" == "null" ]]; then
    echo "artifact upload did not return artifact.id" >&2
    exit 1
  fi

  UPLOADED_ARTIFACT_IDS_JSON="$(jq -n --arg id "$artifact_id" '[$id]')"
}

submit_turn() {
  local text="$1"
  local artifact_ids_json="$2"
  local responds_to_input_request_id="${3:-}"
  local client_turn_id="turn-$(date +%s)-$RANDOM"
  local payload
  local turn_response
  local event_stream_path

  print_section "Submit Turn"
  if [[ -n "$responds_to_input_request_id" ]]; then
    payload="$(
      jq -n \
        --arg schema_version "ai_turn.v1" \
        --arg client_turn_id "$client_turn_id" \
        --arg text "$text" \
        --arg responds_to_input_request_id "$responds_to_input_request_id" \
        --argjson artifact_ids "$artifact_ids_json" \
        '{schema_version:$schema_version, client_turn_id:$client_turn_id, message:{text:$text, artifact_ids:$artifact_ids}, responds_to_input_request_id:$responds_to_input_request_id}'
    )"
  else
    payload="$(
      jq -n \
        --arg schema_version "ai_turn.v1" \
        --arg client_turn_id "$client_turn_id" \
        --arg text "$text" \
        --argjson artifact_ids "$artifact_ids_json" \
        '{schema_version:$schema_version, client_turn_id:$client_turn_id, message:{text:$text, artifact_ids:$artifact_ids}}'
    )"
  fi

  turn_response="$(api_post_json "$BASE_URL/v1/repair-sessions/$SESSION_ID/turns" "$payload")"
  echo "$turn_response" | jq

  event_stream_path="$(jq -r '.event_stream_url' <<<"$turn_response")"
  if [[ -z "$event_stream_path" || "$event_stream_path" == "null" ]]; then
    echo "turn response did not return event_stream_url" >&2
    exit 1
  fi

  if [[ "$REPLAY_AFTER" == "returned" ]]; then
    NEXT_STREAM_PATH="$event_stream_path"
    return
  fi

  NEXT_STREAM_PATH="/v1/repair-sessions/$SESSION_ID/events?after=$REPLAY_AFTER"
}

stream_events_once() {
  local stream_path="$1"
  local output_file="$TMP_DIR/events-$(date +%s)-$RANDOM.txt"

  if [[ "$stream_path" == *\?* ]]; then
    stream_path="${stream_path}&timeout_seconds=$STREAM_TIMEOUT_SECONDS"
  else
    stream_path="${stream_path}?timeout_seconds=$STREAM_TIMEOUT_SECONDS"
  fi

  print_section "Stream Events"
  echo "session_id=$SESSION_ID"
  echo "GET $BASE_URL$stream_path"

  curl --fail-with-body -sS -N "$BASE_URL$stream_path" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: text/event-stream" | tee "$output_file"

  parse_sse_file "$output_file"
}

parse_sse_file() {
  local file="$1"
  local current_event=""
  local data_json=""
  local message_text=""
  local input_prompt=""

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" ]]; then
      current_event=""
      data_json=""
      continue
    fi

    case "$line" in
      id:\ *)
        LAST_EVENT_ID="${line#id: }"
        ;;
      event:\ *)
        current_event="${line#event: }"
        ;;
      data:\ *)
        data_json="${line#data: }"
        case "$current_event" in
          assistant.delta)
            ;;
          assistant.message.completed)
            message_text="$(jq -r '.data.full_text' <<<"$data_json")"
            print_section "Assistant Message"
            printf '%s\n' "$message_text"
            ;;
          input.requested)
            CURRENT_INPUT_REQUEST_ID="$(jq -r '.data.input_request.id' <<<"$data_json")"
            input_prompt="$(jq -r '.data.input_request.prompt' <<<"$data_json")"
            print_section "Input Requested"
            echo "input_request_id=$CURRENT_INPUT_REQUEST_ID"
            printf '%s\n' "$input_prompt"
            ;;
          phase.report.created)
            CURRENT_DIAGNOSTIC_REPORT_ID="$(jq -r '.data.report_id' <<<"$data_json")"
            print_section "Report Created"
            echo "report_id=$CURRENT_DIAGNOSTIC_REPORT_ID"
            jq '.data' <<<"$data_json"
            ;;
          safety.escalated)
            print_section "Safety Escalated"
            jq '.data' <<<"$data_json"
            ;;
          error)
            print_section "Error Event"
            jq '.data' <<<"$data_json"
            ;;
          turn.completed)
            CURRENT_STATUS="$(jq -r '.data.session.status' <<<"$data_json")"
            print_section "Turn Completed"
            echo "status=$CURRENT_STATUS"
            ;;
        esac
        ;;
    esac
  done < "$file"
}

refresh_session_state() {
  local session_response

  print_section "Session State"
  session_response="$(api_get "$BASE_URL/v1/repair-sessions/$SESSION_ID")"
  echo "$session_response" | jq

  CURRENT_STATUS="$(jq -r '.status' <<<"$session_response")"
  CURRENT_INPUT_REQUEST_ID="$(jq -r '.current_input_request.id // empty' <<<"$session_response")"
  CURRENT_DIAGNOSTIC_REPORT_ID="$(jq -r '.latest_reports.diagnostic_report_id // empty' <<<"$session_response")"
}

print_latest_report() {
  local report_response

  if [[ -z "$CURRENT_DIAGNOSTIC_REPORT_ID" || "$CURRENT_DIAGNOSTIC_REPORT_ID" == "$LAST_PRINTED_REPORT_ID" ]]; then
    return
  fi

  print_section "Diagnostic Report"
  report_response="$(api_get "$BASE_URL/v1/repair-sessions/$SESSION_ID/reports/$CURRENT_DIAGNOSTIC_REPORT_ID")"
  echo "$report_response" | jq
  LAST_PRINTED_REPORT_ID="$CURRENT_DIAGNOSTIC_REPORT_ID"
}

wait_for_turn_progress() {
  local stream_path="$1"
  local round=1

  while [[ "$round" -le "$MAX_STREAM_ROUNDS" ]]; do
    stream_events_once "$stream_path"
    refresh_session_state
    print_latest_report

    if [[ "$CURRENT_STATUS" != "running" ]]; then
      return
    fi

    stream_path="/v1/repair-sessions/$SESSION_ID/events?after=$LAST_EVENT_ID"
    round=$((round + 1))
  done
}

prompt_for_follow_up() {
  local follow_up_text
  local follow_up_image
  print_section "Reply"
  printf 'Enter follow-up text (leave blank to stop): '
  IFS= read -r follow_up_text

  if [[ -z "$follow_up_text" ]]; then
    return 1
  fi

  printf 'Optional image path (leave blank for none): '
  IFS= read -r follow_up_image

  if [[ -n "$follow_up_image" ]]; then
    upload_artifact "$follow_up_image" "art-$(date +%s)-$RANDOM"
  else
    UPLOADED_ARTIFACT_IDS_JSON='[]'
  fi

  submit_turn "$follow_up_text" "$UPLOADED_ARTIFACT_IDS_JSON" "$CURRENT_INPUT_REQUEST_ID"
  wait_for_turn_progress "$NEXT_STREAM_PATH"
  return 0
}

require_command curl
require_command jq
require_command tee

if [[ "$STREAM_TIMEOUT_SECONDS" -lt 6 || "$STREAM_TIMEOUT_SECONDS" -gt 120 ]]; then
  echo "STREAM_TIMEOUT_SECONDS must be between 6 and 120 for live event streaming" >&2
  exit 1
fi

print_section "Config"
cat <<EOF
BASE_URL=$BASE_URL
BIKE_ID=$BIKE_ID
TEXT=$TEXT
IMAGE=${IMAGE:-<none>}
STREAM_TIMEOUT_SECONDS=$STREAM_TIMEOUT_SECONDS
REPLAY_AFTER=$REPLAY_AFTER
MAX_STREAM_ROUNDS=$MAX_STREAM_ROUNDS
EOF

print_section "Create Session"
SESSION_RESPONSE="$(
  api_post_json \
    "$BASE_URL/v1/repair-sessions" \
    "$(jq -n --arg bike_id "$BIKE_ID" --arg client_session_id "$CLIENT_SESSION_ID" '{bike_id:$bike_id, client_session_id:$client_session_id}')"
)"
echo "$SESSION_RESPONSE" | jq
SESSION_ID="$(jq -r '.id' <<<"$SESSION_RESPONSE")"

if [[ -z "$SESSION_ID" || "$SESSION_ID" == "null" ]]; then
  echo "session creation did not return an id" >&2
  exit 1
fi

if [[ -n "$IMAGE" ]]; then
  upload_artifact "$IMAGE" "art-$(date +%s)-$RANDOM"
fi

submit_turn "$TEXT" "$UPLOADED_ARTIFACT_IDS_JSON"
wait_for_turn_progress "$NEXT_STREAM_PATH"

while [[ -n "$CURRENT_INPUT_REQUEST_ID" && "$CURRENT_STATUS" == "awaiting_user" ]]; do
  if ! prompt_for_follow_up; then
    break
  fi
done

print_section "Done"
echo "Diagnostic demo completed for session $SESSION_ID"
