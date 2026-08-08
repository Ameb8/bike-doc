"""HTTP boundary middleware owned by the BikeDoc application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response

logger = structlog.get_logger(__name__)


def install_request_logging(app: FastAPI) -> None:
    """Install request correlation and the app-owned access lifecycle log."""

    @app.middleware("http")
    async def request_logging(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started_at = perf_counter()
        base_fields = {
            "method": request.method,
            "path": request.url.path,
            "client_host": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
        }
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            fields = {
                **base_fields,
                "route": _route_path(request),
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started_at) * 1000),
            }
            if response.status_code < 400:
                logger.info("http_request_completed", **fields)
            elif response.status_code < 500:
                logger.warning("http_request_completed", **fields)
            else:
                logger.error("http_request_completed", **fields)
            return response
        except Exception:
            logger.exception(
                "http_request_failed",
                **base_fields,
                route=_route_path(request),
                status_code=500,
                duration_ms=round((perf_counter() - started_at) * 1000),
            )
            raise
        finally:
            structlog.contextvars.clear_contextvars()


def _route_path(request: Request) -> str | None:
    route = request.scope.get("route")
    return getattr(route, "path", None)
