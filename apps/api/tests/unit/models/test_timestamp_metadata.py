"""Persistence timestamp metadata tests."""

from __future__ import annotations

from sqlalchemy import DateTime

import bike_doc_api.models  # noqa: F401
from bike_doc_api.db.base import Base


def test_datetime_columns_are_timezone_aware() -> None:
    """Keep ORM timestamp bindings aligned with PostgreSQL timestamptz columns."""

    datetime_columns = [
        column
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, DateTime)
    ]

    assert datetime_columns
    assert all(column.type.timezone for column in datetime_columns)
