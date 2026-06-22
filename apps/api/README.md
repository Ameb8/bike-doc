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
