"""Database and official NATS adapters; resource construction stays in the host."""

from collections.abc import Callable, Sequence

from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bike_doc_api.repositories.background_jobs import (
    BackgroundJobRepository,
    DeliveryResolution,
    ExecutionPolicy,
    JobError,
    JobOutcome,
    JobSnapshot,
)
from bike_doc_api.workers.registry import DeliveryEnvelope
from bike_doc_api.workers.runtime import Delivery


class PostgresJobStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def resolve(
        self,
        envelope: DeliveryEnvelope,
        workload: str,
        validate: Callable[[JobSnapshot], ExecutionPolicy | JobError],
    ) -> DeliveryResolution:
        async with self.sessions() as session, session.begin():
            result = await BackgroundJobRepository(session).resolve_delivery(
                job_id=envelope.job_id,
                generation=envelope.publication_generation,
                workload_class=workload,
                validate_definition=validate,
            )
        return result

    async def finish(self, job: JobSnapshot, outcome: JobOutcome) -> JobSnapshot | None:
        assert job.execution_token
        async with self.sessions() as session, session.begin():
            result = await BackgroundJobRepository(session).apply_outcome_and_load(
                job_id=job.id, execution_token=job.execution_token, outcome=outcome
            )
        return result


class NatsDelivery:
    def __init__(self, message: Msg) -> None:
        self._message = message

    @property
    def subject(self) -> str:
        return self._message.subject

    @property
    def data(self) -> bytes:
        return self._message.data

    async def ack(self) -> None:
        await self._message.ack_sync(timeout=3)

    async def nak(self, delay: float) -> None:
        await self._message.nak(delay=delay)

    async def terminate(self) -> None:
        await self._message.term()

    async def progress(self) -> None:
        await self._message.in_progress()


class NatsPullTransport:
    def __init__(
        self, subscription: JetStreamContext.PullSubscription, *, client: Client
    ) -> None:
        self.subscription = subscription
        self.client = client

    async def fetch(self, batch: int, wait_seconds: float) -> Sequence[Delivery]:
        try:
            return [
                NatsDelivery(message)
                for message in await self.subscription.fetch(
                    batch, timeout=wait_seconds
                )
            ]
        except NatsTimeoutError:
            return []

    async def close(self) -> None:
        # The process host still closes its connection context after this drain.
        await self.subscription.unsubscribe()
        await self.client.drain()


async def subscribe_work_advisories(client: Client, stream: str, consumer: str) -> None:
    """Process-owned observation only. No job-store access or payload logging."""
    import structlog

    logger = structlog.get_logger(__name__)

    async def maximum_delivery(_message: Msg) -> None:
        logger.warning("job_transport_advisory", advisory="maximum_delivery")

    async def terminated(_message: Msg) -> None:
        logger.warning("job_transport_advisory", advisory="terminated")

    await client.subscribe(
        f"$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.{stream}.{consumer}",
        cb=maximum_delivery,
    )
    await client.subscribe(
        f"$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.{stream}.{consumer}", cb=terminated
    )
