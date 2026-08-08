"""Logging setup."""

import logging
import sys

import structlog


def configure_logging(
    *,
    environment: str,
    log_level: str | None = None,
    log_format: str | None = None,
    diagnostic_log_level: str = "INFO",
) -> None:
    """Configure one idempotent structlog/std-lib logging pipeline."""
    del environment  # Level 2 intentionally uses INFO outside diagnostic namespaces.
    level_name = log_level or "INFO"
    level = logging.getLevelNamesMapping()[level_name]
    renderer: structlog.types.Processor
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *shared_processors],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    diagnostic_level = logging.getLevelNamesMapping()[diagnostic_log_level]
    for name, logger_level in {
        "bike_doc_api": level,
        "bike_doc_api.adk": diagnostic_level,
        "bike_doc_api.services.diagnostic_visual_context": diagnostic_level,
        "uvicorn.error": level,
        "sqlalchemy.engine": logging.WARNING,
        "alembic": logging.INFO,
        "google.adk": logging.WARNING,
        "google_adk.google.adk.models.google_llm": logging.WARNING,
        "google_genai": logging.WARNING,
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "urllib3": logging.WARNING,
        "PIL": logging.WARNING,
    }.items():
        named_logger = logging.getLogger(name)
        named_logger.handlers.clear()
        named_logger.propagate = True
        named_logger.disabled = False
        named_logger.setLevel(logger_level)

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.disabled = True
