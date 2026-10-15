"""Finite read-only inspection. Broker observations never change application truth."""

import argparse
import asyncio
import json
import sys
from typing import Never

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bike_doc_api.core.config import Settings, get_settings
from bike_doc_api.operations.monitoring import QueueMonitor
from bike_doc_api.repositories.job_inspection import (
    InspectionFilter,
    JobInspectionRepository,
)


async def inspect_jobs(
    filters: InspectionFilter,
    *,
    transport: bool = False,
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            database = await JobInspectionRepository(session).page(filters)
        observations: dict[str, object] = {"status": "not_requested"}
        if transport:
            try:
                async with asyncio.timeout(10):
                    observations = await QueueMonitor(settings).snapshot()
            except Exception:
                observations = {"status": "unavailable"}
        return {
            "postgresql_truth": database,
            "jetstream_transport_observations": observations,
        }
    finally:
        await engine.dispose()


class InspectionParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        self.exit(2, "Invalid inspection arguments\n")


def main() -> None:
    parser = InspectionParser(description=__doc__)
    parser.add_argument("--transport", action="store_true")
    parser.add_argument(
        "--filters", default="{}", help="JSON object of bounded operational filters"
    )
    args = parser.parse_args()
    try:
        filters = InspectionFilter.model_validate_json(args.filters)
    except ValidationError:
        parser.exit(2, "Invalid operational filters\n")
    try:
        result = asyncio.run(inspect_jobs(filters, transport=args.transport))
    except KeyboardInterrupt:
        parser.exit(130, "Inspection interrupted\n")
    except Exception:
        parser.exit(1, "Inspection unavailable\n")
    sys.stdout.write(json.dumps(result, default=str, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
