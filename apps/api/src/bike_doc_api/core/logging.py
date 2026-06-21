"""Logging setup."""

import logging


def configure_logging(environment: str) -> None:
    """Configure process logging for local development and deployment."""
    level = logging.DEBUG if environment == "local" else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
