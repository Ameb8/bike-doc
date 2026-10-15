#!/usr/bin/env bash
set -euo pipefail

# --- Commit 1: publish canonical route metadata ---
# Keep route operation IDs, response envelopes, and SSE metadata together at the HTTP boundary.
git add -- \
  apps/api/src/bike_doc_api/api/responses.py \
  apps/api/src/bike_doc_api/api/v1/artifacts.py \
  apps/api/src/bike_doc_api/api/v1/events.py \
  apps/api/src/bike_doc_api/api/v1/repair_sessions.py \
  apps/api/src/bike_doc_api/api/v1/reports.py \
  apps/api/src/bike_doc_api/api/v1/turns.py
git commit -F - <<'EOF'
fix(api): publish canonical route metadata

Describe canonical operation IDs, public error envelopes, and SSE response metadata on implemented routes. Share error response models through the API response helper.

Co-Authored-By: OpenAI GPT-5.6-Terra <noreply@openai.com>
EOF

# --- Commit 2: align diagnostic schemas with OpenAPI ---
# Keep typed report schemas, schema validation, the active contract gate, and its documentation together.
git add -- \
  apps/api/ARCHITECTURE.md \
  apps/api/src/bike_doc_api/adk/tools/reports.py \
  apps/api/src/bike_doc_api/schemas/artifact.py \
  apps/api/src/bike_doc_api/schemas/report.py \
  apps/api/tests/contract/test_openapi_contract.py \
  apps/api/tests/unit/schemas/test_diagnostic_schemas.py
git commit -F - <<'EOF'
fix(api): align diagnostic schemas with openapi

Use typed, version-discriminated report payloads and publish their validation constraints. Activate the resolved contract gate across implemented diagnostic operations and add regression coverage for schema drift.

Co-Authored-By: OpenAI GPT-5.6-Terra <noreply@openai.com>
EOF

# --- Coverage Summary ---
# apps/api/src/bike_doc_api/api/responses.py: Commit 1
# apps/api/src/bike_doc_api/api/v1/artifacts.py: Commit 1
# apps/api/src/bike_doc_api/api/v1/events.py: Commit 1
# apps/api/src/bike_doc_api/api/v1/repair_sessions.py: Commit 1
# apps/api/src/bike_doc_api/api/v1/reports.py: Commit 1
# apps/api/src/bike_doc_api/api/v1/turns.py: Commit 1
# apps/api/ARCHITECTURE.md: Commit 2
# apps/api/src/bike_doc_api/adk/tools/reports.py: Commit 2
# apps/api/src/bike_doc_api/schemas/artifact.py: Commit 2
# apps/api/src/bike_doc_api/schemas/report.py: Commit 2
# apps/api/tests/contract/test_openapi_contract.py: Commit 2
# apps/api/tests/unit/schemas/test_diagnostic_schemas.py: Commit 2
git diff --stat HEAD
