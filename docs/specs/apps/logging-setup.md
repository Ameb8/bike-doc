# Bike Doc API Logging Setup Spec

Status: Draft v0.1
Last updated: 2026-06-22

This spec defines the initial logging setup for the Bike Doc FastAPI backend.
It covers process logging configuration, request logging middleware, and the
developer-facing logging API. It is intentionally small: after setup, normal
application code should only need `structlog.get_logger(__name__)` and
structured keyword fields.

## References

- Backend scaffold: `docs/specs/apps/api.md`
- API root: `apps/api`

## Goals

- Use `structlog` as the application logging interface.
- Produce readable console logs in local development.
- Produce structured JSON logs in production-like environments.
- Replace `uvicorn.access` with one app-owned structured request log emitted by
  FastAPI middleware.
- Route stdlib, Uvicorn error, SQLAlchemy, Alembic, and third-party logs through
  the same process logging configuration.
- Keep logging easy to use from routes, services, repositories, providers, and
  future ADK integration code.

## Non-Goals

- Do not define ADK-specific agent, model, or tool-call logging fields here.
- Do not add a durable audit/event log. Product events remain separate from
  process logs.
- Do not design logging tests in this spec.
- Do not require feature code to pass logger instances through function
  signatures.

## Dependency

Add `structlog` to `apps/api/pyproject.toml` runtime dependencies:

```toml
dependencies = [
  "structlog>=25",
]
```

The exact resolved version belongs in `apps/api/uv.lock`.

## Settings

Extend `bike_doc_api.core.config.Settings` with logging settings:

```python
log_level: str | None = None
log_format: str | None = None
```

Derived defaults:

| Setting | Local default | Non-local default | Allowed values |
|---|---:|---:|---|
| `log_level` | `DEBUG` | `INFO` | stdlib level names |
| `log_format` | `console` | `json` | `console`, `json` |

The existing `environment` setting decides local vs non-local behavior. Treat
`environment == "local"` as local development; all other values use
production-like defaults.

## Logger Usage

Application code should use module-level loggers:

```python
import structlog

logger = structlog.get_logger(__name__)
```

Log messages should use stable event names and structured keyword fields:

```python
logger.info("repair_session_created", repair_session_id=session_id, user_id=user_id)
```

Do not build reusable wrapper classes around structlog unless a concrete need
appears. Bound context should use structlog contextvars, not custom global
state.

## Configuration Entry Point

Keep logging setup owned by `bike_doc_api.core.logging.configure_logging`.

Target signature:

```python
def configure_logging(
    *,
    environment: str,
    log_level: str | None = None,
    log_format: str | None = None,
) -> None:
    ...
```

`create_app()` should call `configure_logging(...)` before constructing the
FastAPI app. Configuration must be idempotent enough for tests and app factory
usage; repeated calls should replace the active logging configuration instead
of stacking handlers.

## Structlog Configuration

Use one shared processor chain for structlog and stdlib logs.

Required processors:

```text
structlog.contextvars.merge_contextvars
structlog.processors.add_log_level
structlog.processors.TimeStamper(fmt="iso", utc=True)
structlog.processors.StackInfoRenderer
structlog.processors.format_exc_info
```

For console output, finish with:

```text
structlog.dev.ConsoleRenderer
```

For JSON output, finish with:

```text
structlog.processors.JSONRenderer
```

Use `structlog.stdlib.LoggerFactory` and `structlog.stdlib.BoundLogger` so
stdlib and structlog behavior stay aligned.

Configure stdlib logging with a single stdout handler. The handler should use
`structlog.stdlib.ProcessorFormatter` so logs from standard `logging` callers
are rendered with the same final renderer.

## Logger Levels

Apply the configured log level to the root logger.

Set package and framework logger levels deliberately:

| Logger | Level |
|---|---|
| root | configured log level |
| `bike_doc_api` | configured log level |
| `uvicorn.error` | configured log level |
| `uvicorn.access` | disabled |
| `sqlalchemy.engine` | `WARNING` |
| `alembic` | `INFO` |

SQL query logging should not be part of the initial setup. If needed later, add
a dedicated setting rather than relying on the global log level.

## Request Logging Middleware

Add a FastAPI middleware for app-owned HTTP request logs. It replaces
`uvicorn.access`.

Middleware responsibilities:

- Generate a request ID when the inbound request does not provide one.
- Reuse an inbound `X-Request-ID` header when present.
- Bind request context with `structlog.contextvars.bind_contextvars`.
- Add `X-Request-ID` to the response.
- Emit one completion log for each request that reaches the app.
- Clear structlog contextvars after the request completes.

Use this event name:

```text
http_request_completed
```

Required fields:

| Field | Description |
|---|---|
| `request_id` | Request correlation ID. |
| `method` | HTTP method. |
| `path` | Raw URL path. |
| `route` | Matched FastAPI route path when available, otherwise `null`. |
| `status_code` | Response status code. |
| `duration_ms` | Request duration rounded to an integer. |

The middleware may also include:

| Field | Description |
|---|---|
| `client_host` | Direct client host if available. |
| `user_agent` | Request user-agent header. |

Expected log levels:

| Status | Level |
|---|---|
| `< 400` | `info` |
| `400-499` | `warning` |
| `>= 500` | `error` |

If an unhandled exception occurs, the middleware should log the same request
fields with `logger.exception("http_request_failed", ...)` and re-raise the
exception so FastAPI error handling remains authoritative.

## Uvicorn Access Logs

Disable Uvicorn access logs for local and production app startup.

Local command:

```sh
uv run uvicorn bike_doc_api.main:app --reload --no-access-log
```

Docker development command should include:

```text
--no-access-log
```

Keep `uvicorn.error` enabled and routed through the shared logging setup. It
should continue to report startup, shutdown, bind, reload, and protocol-level
server errors.

## Initial File Placement

Expected implementation locations:

```text
apps/api/src/bike_doc_api/core/logging.py
apps/api/src/bike_doc_api/core/config.py
apps/api/src/bike_doc_api/api/middleware.py
apps/api/src/bike_doc_api/main.py
apps/api/pyproject.toml
apps/api/Dockerfile.dev
docker-compose.yml
```

`api/middleware.py` should expose a small installer function, for example:

```python
def install_request_logging(app: FastAPI) -> None:
    ...
```

`main.py` should call it during app creation after the FastAPI instance is
constructed and before routers are included.

## Acceptance Criteria

- `structlog` is the only logging API used by application code.
- Local development logs are readable without external tooling.
- Non-local logs are JSON objects written to stdout.
- `uvicorn.access` does not emit duplicate request logs.
- Every app-handled HTTP request emits exactly one
  `http_request_completed` log unless it fails with an unhandled exception, in
  which case it emits `http_request_failed`.
- Response headers include `X-Request-ID`.
- Existing stdlib logs still appear through the configured renderer.
