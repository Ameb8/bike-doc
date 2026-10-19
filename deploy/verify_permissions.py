"""Run inside the private network against the generated real broker credentials."""

import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

import nats

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import ensure_work_topology


async def main() -> None:
    clients = {}
    errors = {}
    changed = {}

    async def wait_errors(role: str, count: int) -> None:
        async with asyncio.timeout(2):
            while len(errors[role]) < count:
                changed[role].clear()
                await changed[role].wait()

    async with AsyncExitStack() as stack:
        for role in ("api", "diagnostic", "profile", "monitor"):
            values = json.loads(
                await asyncio.to_thread(Path(f"/verification/{role}.json").read_text)
            )
            errors[role] = []
            changed[role] = asyncio.Event()

            async def receive(error: Exception, role: str = role) -> None:
                errors[role].append(type(error).__name__)
                changed[role].set()

            client = await nats.connect(
                values["BIKE_DOC_API_NATS_URL"],
                inbox_prefix=values["BIKE_DOC_API_NATS_INBOX_PREFIX"].encode(),
                error_cb=receive,
            )
            stack.push_async_callback(client.close)
            clients[role] = client
        api = clients["api"]
        settings = Settings()
        await ensure_work_topology(api.jetstream(), settings)
        for role in ("diagnostic", "profile"):
            client = clients[role]
            js = client.jetstream(timeout=0.4)
            subject = f"bikedoc.work.v1.{role}"
            durable = f"bikedoc_{role}_v1"
            await ensure_work_topology(
                js,
                settings,
                workload="diagnostic" if role == "diagnostic" else "profile_inference",
            )
            await api.jetstream().publish(subject, b'{"version":1}')
            sub = await js.pull_subscribe(
                subject,
                durable=durable,
                stream=settings.nats_work_stream,
                inbox_prefix=f"_INBOX.{role}.".encode(),
            )
            message = (await sub.fetch(1, timeout=2))[0]
            await message.ack_sync(timeout=2)
            other = "profile" if role == "diagnostic" else "diagnostic"
            before = len(errors[role])
            for prohibited in (
                f"$JS.API.CONSUMER.MSG.NEXT.BIKEDOC_WORK_V1.bikedoc_{other}_v1",
                f"$JS.API.CONSUMER.INFO.BIKEDOC_WORK_V1.bikedoc_{other}_v1",
                f"$JS.ACK.BIKEDOC_WORK_V1.bikedoc_{other}_v1.1.1.1.1.0",
                f"bikedoc.work.v1.{other}",
            ):
                try:
                    await client.request(prohibited, b"{}", timeout=0.3)
                except (TimeoutError, nats.errors.NoRespondersError):
                    pass
                else:
                    raise AssertionError("cross-role operation unexpectedly authorized")
            await client.subscribe(f"bikedoc.work.v1.{other}")
            await client.subscribe(f"_INBOX.{other}.>")
            await client.flush()
            await wait_errors(role, before + 6)
        # Wake-up publisher/subscriber split. Profile and monitor cannot publish.
        received = asyncio.Event()

        async def wakeup(message: object) -> None:
            received.set()

        await api.subscribe("bikedoc.events.wakeup.v1", cb=wakeup)
        await api.flush()
        await clients["diagnostic"].publish("bikedoc.events.wakeup.v1", b"{}")
        await clients["diagnostic"].flush()
        await asyncio.wait_for(received.wait(), 2)
        for role in ("profile", "monitor"):
            before = len(errors[role])
            await clients[role].publish("bikedoc.events.wakeup.v1", b"{}")
            await clients[role].flush()
            await wait_errors(role, before + 1)
        # Wake-up consumption belongs only to API hosts; workers publish hints
        # without receiving other sessions' hints. Monitoring cannot execute work.
        for role in ("diagnostic", "profile", "monitor"):
            before = len(errors[role])
            await clients[role].subscribe("bikedoc.events.wakeup.v1")
            await clients[role].flush()
            await wait_errors(role, before + 1)
        for role in ("api", "monitor"):
            before = len(errors[role])
            await clients[role].publish(
                "$JS.API.CONSUMER.MSG.NEXT.BIKEDOC_WORK_V1.bikedoc_diagnostic_v1",
                b'{"batch":1}',
            )
            await clients[role].flush()
            await wait_errors(role, before + 1)
        monitor = clients["monitor"].jetstream()
        await monitor.stream_info(settings.nats_work_stream)
        await monitor.consumer_info(
            settings.nats_work_stream, settings.nats_profile_consumer
        )
        # Provisioning is restricted to the two named durable consumers. Denial
        # must come from subject authorization, rather than an invalid body.
        before = len(errors["api"])
        for prohibited in (
            f"$JS.API.CONSUMER.CREATE.{settings.nats_work_stream}",
            f"$JS.API.CONSUMER.DURABLE.CREATE.{settings.nats_work_stream}.unexpected",
            f"$JS.API.CONSUMER.CREATE.{settings.nats_work_stream}.unexpected.bikedoc.work.v1.diagnostic",
        ):
            await api.publish(prohibited, b"{}")
        await api.flush()
        await wait_errors("api", before + 3)
        try:
            await ensure_work_topology(
                api.jetstream(),
                settings.model_copy(update={"nats_work_max_age_seconds": 1}),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("incompatible topology accepted")
    print("Real NATS role permissions, SSE, settlement and topology checks passed")  # noqa: T201


asyncio.run(main())
