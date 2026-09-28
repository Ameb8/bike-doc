"""Process-owned durable ADK session storage and migration compatibility."""

from __future__ import annotations

from google.adk.sessions import DatabaseSessionService
from google.adk.sessions.migration import _schema_check_utils
from google.adk.sessions.schemas.v1 import Base as ADKSchemaV1
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from bike_doc_api.core.config import Settings

EXPECTED_ADK_SCHEMA_VERSION = "1"


def _check_schema(connection: Connection) -> None:
    """Reject absent, old, or partial ADK storage before ADK's lazy create_all."""

    if _schema_check_utils.LATEST_SCHEMA_VERSION != EXPECTED_ADK_SCHEMA_VERSION:
        raise RuntimeError(
            "Pinned ADK storage version changed; review and migrate its "
            "schema before startup"
        )

    inspector = inspect(connection)
    expected_tables = ADKSchemaV1.metadata.tables
    if not set(expected_tables).issubset(inspector.get_table_names()):
        raise RuntimeError("ADK session schema is missing; run alembic upgrade head")

    version = connection.execute(
        text("SELECT value FROM adk_internal_metadata WHERE key = 'schema_version'")
    ).scalar_one_or_none()
    if version != EXPECTED_ADK_SCHEMA_VERSION:
        raise RuntimeError(
            "Incompatible ADK session schema version; migrate storage "
            "for google-adk 2.3.0"
        )

    for name, table in expected_tables.items():
        actual_columns = {
            column["name"]: column for column in inspector.get_columns(name)
        }
        if set(actual_columns) != set(table.columns.keys()):
            raise RuntimeError(
                f"Incomplete ADK session schema ({name}); run migrations"
            )
        for column in table.columns:
            actual = actual_columns[column.name]
            expected_type = column.type.compile(dialect=connection.dialect)
            actual_type = actual["type"].compile(dialect=connection.dialect)
            if actual_type != expected_type or actual["nullable"] != column.nullable:
                raise RuntimeError(
                    f"Incompatible ADK session column ({name}); run migrations"
                )
        expected_pk = {column.name for column in table.primary_key}
        actual_pk = set(inspector.get_pk_constraint(name)["constrained_columns"])
        if actual_pk != expected_pk:
            raise RuntimeError(f"Incompatible ADK session key ({name}); run migrations")

    indexes = {index["name"]: index for index in inspector.get_indexes("events")}
    event_index = indexes.get("idx_events_app_user_session_ts")
    if event_index is None or event_index["column_names"] != [
        "app_name",
        "user_id",
        "session_id",
        "timestamp",
    ]:
        raise RuntimeError("Incomplete ADK event index; run migrations")
    index_ddl = connection.execute(
        text(
            "SELECT indexdef FROM pg_indexes WHERE tablename='events' "
            "AND indexname='idx_events_app_user_session_ts'"
        )
    ).scalar_one_or_none()
    if index_ddl is None or '"timestamp" DESC' not in index_ddl:
        raise RuntimeError("Incompatible ADK event index; run migrations")
    fks = inspector.get_foreign_keys("events")
    if not any(
        fk["referred_table"] == "sessions"
        and fk["constrained_columns"] == ["app_name", "user_id", "session_id"]
        and fk["options"].get("ondelete") == "CASCADE"
        for fk in fks
    ):
        raise RuntimeError("Incomplete ADK event foreign key; run migrations")

    if (
        connection.execute(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname = "
                "'trg_protect_bound_adk_session' AND tgrelid = 'sessions'::regclass "
                "AND NOT tgisinternal"
            )
        ).scalar_one()
        != 1
    ):
        raise RuntimeError("ADK retention guard is missing; run migrations")


async def open_adk_session_service(settings: Settings) -> DatabaseSessionService:
    """Validate deployed schema and create one closeable service for this process."""

    try:
        service = DatabaseSessionService(db_url=settings.database_url)
    except ValueError:
        raise RuntimeError(
            "Unable to initialize ADK PostgreSQL storage; check database configuration"
        ) from None
    try:
        if service.db_engine.dialect.name != "postgresql":
            raise RuntimeError("ADK session storage requires PostgreSQL")
        async with service.db_engine.connect() as connection:
            await connection.run_sync(_check_schema)
        return service
    except BaseException:
        await service.close()
        raise
