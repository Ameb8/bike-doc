"""Logging setup."""

import logging
import sys


def configure_logging(
    *,
    environment: str,
    log_level: str | None = None,
    log_format: str | None = None,
) -> None:
    """Configure process logging for local development and deployment."""
    level_name = log_level or ("DEBUG" if environment == "local" else "INFO")
    level = logging.getLevelNamesMapping()[level_name]
    format_string = (
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        if log_format != "json"
        else '{"timestamp":"%(asctime)s","level":"%(levelname)s",'
        '"logger":"%(name)s","message":"%(message)s"}'
    )

    logging.basicConfig(
        level=level,
        format=format_string,
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("bike_doc_api").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.INFO)
