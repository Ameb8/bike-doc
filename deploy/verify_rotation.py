"""Verify revoked credentials fail without emitting URLs or transport errors."""

import asyncio
from pathlib import Path

import nats


async def main() -> None:
    async def ignore(error: Exception) -> None:
        pass

    try:
        client = await nats.connect(
            await asyncio.to_thread(Path("/verification/retired-api-url").read_text),
            max_reconnect_attempts=1,
            error_cb=ignore,
        )
    except nats.errors.Error:
        print("Retired API credentials rejected")  # noqa: T201
    else:
        await client.close()
        raise AssertionError("Revoked credentials remained authorized")


asyncio.run(main())
