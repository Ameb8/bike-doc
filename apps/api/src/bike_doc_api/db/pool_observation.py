"""Safe QueuePool measurements without SQL, URLs or connection identity."""

from contextlib import suppress

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import QueuePool

from bike_doc_api.core.queue_telemetry import QueueTelemetry, queue_telemetry


def observe_pool(
    engine: AsyncEngine, role: str, telemetry: QueueTelemetry = queue_telemetry
) -> None:
    pool = engine.sync_engine.pool
    if not isinstance(pool, QueuePool):
        return

    def sample(returning: bool = False) -> None:
        with suppress(Exception):
            for field, value in (
                ("size", pool.size()),
                ("checked_out", max(0, pool.checkedout() - int(returning))),
                ("overflow", max(0, pool.overflow())),
            ):
                telemetry.observe("pool", value, role=role, pool=field)

    def checkout(*_args: object) -> None:
        sample()

    def checkin(*_args: object) -> None:
        # SQLAlchemy calls checkin listeners immediately before returning the slot.
        sample(returning=True)

    event.listen(pool, "checkout", checkout)
    event.listen(pool, "checkin", checkin)


def sample_session_pool(session: AsyncSession, role: str) -> None:
    bind = session.bind
    if isinstance(bind, AsyncEngine) and isinstance(bind.pool, QueuePool):
        pool = bind.pool
        for field, value in (
            ("size", pool.size()),
            ("checked_out", pool.checkedout()),
            ("overflow", max(0, pool.overflow())),
        ):
            queue_telemetry.observe("pool", value, role=role, pool=field)
