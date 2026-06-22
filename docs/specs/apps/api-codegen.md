# Bike Doc API Codegen Spec

Status: Draft v0.1
Last updated: 2026-06-22

`docs/specs/openapi.yaml` is the canonical public API/frontend contract.

For the diagnostic slice, generated code is used only as a contract check and
implementation reference. The final backend does not import generated code.

## Policy

- Generate into a temporary directory for spikes and drift checks.
- Do not commit generated files.
- Do not use generated routers in `apps/api/src/bike_doc_api/api/v1`.
- Do not import generated Pydantic models from application code.
- Keep route modules, schemas, services, repositories, ADK tools, storage
  behavior, and SSE streaming hand-written.
- Use contract tests to keep hand-written API schemas aligned with
  `docs/specs/openapi.yaml`.

## Command

Run from `apps/api`, using a temporary output path:

```bash
uv run --group codegen fastapi-codegen \
  --input ../../docs/specs/openapi.yaml \
  --output /private/tmp/bike-doc-codegen-check \
  --generate-routers \
  --output-model-type pydantic_v2.BaseModel \
  --python-version 3.12 \
  --use-annotated \
  --strict-nullable \
  --disable-timestamp
```

The configured package output in `apps/api/pyproject.toml` is retained as a
reference, but should not be used for the diagnostic slice unless this spec is
changed.

## Done

- Codegen succeeds from the canonical OpenAPI file.
- No generated file is committed or imported.
- Hand-written API behavior remains the production implementation.
