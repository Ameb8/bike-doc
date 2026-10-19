FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/bike-doc-api-venv \
    UV_LINK_MODE=copy \
    PATH="/opt/bike-doc-api-venv/bin:${PATH}"
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/README.md ./apps/api/
RUN cd apps/api && uv sync --frozen --no-dev --no-cache --no-install-project
COPY apps/api/alembic.ini ./apps/api/
COPY apps/api/src ./apps/api/src
RUN cd apps/api && uv sync --frozen --no-dev --no-cache --compile-bytecode
WORKDIR /workspace/apps/api
CMD ["python", "-m", "bike_doc_api.operations.deployment", "api"]
