# Bike Doc API Configuration Setup

Status: Draft v0.1
Last updated: 2026-06-22

Backend root: `apps/api`

This spec defines the baseline configuration pattern for the Bike Doc FastAPI
backend. It covers how settings are loaded, shared, and documented. It does not
define feature-specific or provider-specific settings; those should be added by
the specs and implementation work that introduce each feature.

## Goals

- Provide one typed settings object for backend process configuration.
- Keep configuration loading in `bike_doc_api.core.config`.
- Read runtime configuration from environment variables.
- Make app setup, tests, and local development use the same settings path.
- Document available settings in `.env.example`.

## Settings Class

Use `pydantic-settings` with a backend-owned `Settings` class:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BIKE_DOC_API_",
        extra="ignore",
    )
```

The canonical environment variable prefix is `BIKE_DOC_API_`. For example, the
`environment` field is configured by `BIKE_DOC_API_ENVIRONMENT`.

Expose a cached settings accessor:

```python
@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Tests may pass a `Settings` instance directly to `create_app(...)` or clear the
settings cache when they intentionally mutate environment variables.

## Runtime Source

The backend reads process environment variables. Docker Compose may read a
root-level `.env` file, but Compose should pass the API settings explicitly
under the API service's `environment` section.

The application should not depend on a whole `.env` file being mounted or
loaded inside the container.

## Baseline Settings

The baseline settings are cross-cutting app settings only:

| Field | Environment Variable | Purpose |
|---|---|---|
| `app_name` | `BIKE_DOC_API_APP_NAME` | FastAPI application title. |
| `environment` | `BIKE_DOC_API_ENVIRONMENT` | Runtime environment, such as `local`, `test`, or `production`. |
| `debug` | `BIKE_DOC_API_DEBUG` | FastAPI debug mode. |
| `cors_origins` | `BIKE_DOC_API_CORS_ORIGINS` | Local/development CORS origins. |
| `log_level` | `BIKE_DOC_API_LOG_LEVEL` | Optional logging level override. |
| `log_format` | `BIKE_DOC_API_LOG_FORMAT` | Optional logging renderer override. |

Feature-specific settings, such as database, auth, model, storage, or provider
configuration, should be added to `Settings` only when the corresponding feature
is implemented.

## Usage

`create_app()` should resolve settings once and pass needed values into app
setup functions such as logging, middleware, routers, and lifespan resource
initialization.

Route handlers should not instantiate `Settings` or read environment variables
directly. Shared resources and provider clients should receive configuration
from app setup or dependency wiring.

## Adding Settings

When adding a new setting:

1. Add the typed field to `Settings`.
2. Add validation or mode restrictions if the setting is only valid in certain
   environments.
3. Add the variable to `.env.example`.
4. Pass the value into the code that needs it instead of reading the environment
   directly from that code.
