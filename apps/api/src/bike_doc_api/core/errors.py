"""Domain errors and FastAPI exception handler registration."""

from fastapi import FastAPI


def install_exception_handlers(_app: FastAPI) -> None:
    """Register application exception handlers when behavior is implemented."""
