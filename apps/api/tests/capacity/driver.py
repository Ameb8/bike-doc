"""Bounded real HTTP/SSE traffic and independent PostgreSQL/JetStream evidence."""

import asyncio
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from budget import (
    ARRIVAL_INTERVAL_SECONDS,
    CONTENT,
    PATH_SENTINEL,
    ROOT,
    SCENARIOS,
    percentile,
    scenario_budgets,
)
from nats.aio.msg import Msg
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import jetstream, nats_connection
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.models.bike import BikeFactClaim, BikeProfile
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.profile_inference import ProfileInferenceRun
from bike_doc_api.models.repair_session import RepairSession
from bike_doc_api.models.user import User


async def action(name: str) -> None:
    (ROOT / f"action-{name}").touch()
    async with asyncio.timeout(240):
        while not (ROOT / f"done-{name}").exists():  # noqa: ASYNC110
            await asyncio.sleep(0.1)


async def run(settings: Settings, scenario: str) -> None:
    assert scenario in SCENARIOS
    engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    samples = []
    deadlines = {}
    sse_latencies = []
    resumed_sse_latencies = []
    resumed_after: float | None = None
    acceptance = []
    outage_acceptance = []
    restored_acceptance = []
    observed_messages = []
    invalid_messages = []
    recoveries = []
    outage_intents = 0
    outage_pending = 0
    readers = []
    read_ready = []
    read_completed = []
    options = json.loads((ROOT / "control.json").read_text())
    count = 48 if scenario in {"burst", "profile_backlog", "profile_failure"} else 24
    if scenario == "nats_outage":
        count = 48
    if scenario == "timing":
        count = 8
    if scenario == "burst":
        count = 16

    async with (
        nats_connection(settings) as client,
        httpx.AsyncClient(
            base_url="http://gateway:8000/v1",
            headers={"Authorization": f"Bearer {settings.dev_auth_token}"},
            timeout=15,
            limits=httpx.Limits(max_connections=32),
        ) as http,
    ):
        js = jetstream(client)
        # A separate test-only observer credential has subscribe permission only;
        # it never consumes the durable queues or changes production role ACLs.
        observer_values = json.loads(
            await asyncio.to_thread(Path("/verification/observer.json").read_text)
        )
        observer_settings = settings.model_copy(
            update={
                "nats_url": SecretStr(observer_values["BIKE_DOC_API_NATS_URL"]),
                "nats_inbox_prefix": "_INBOX.observer",
            }
        )
        async with nats_connection(observer_settings) as observer:

            async def capture(message: Msg) -> None:
                observed_messages.append(
                    {
                        "subject": message.subject,
                        "payload": message.data.decode("utf-8", errors="replace"),
                        "headers": message.headers,
                    }
                )
                try:
                    payload = json.loads(message.data)
                    assert set(payload) == {
                        "version",
                        "job_id",
                        "publication_generation",
                    }
                    assert set(message.headers or {}) <= {"Nats-Msg-Id"}
                except (ValueError, AssertionError, TypeError):
                    invalid_messages.append(True)

            for subject in (
                settings.nats_diagnostic_subject,
                settings.nats_profile_subject,
            ):
                await observer.subscribe(subject, cb=capture)
            await observer.flush()

            async def monitor() -> None:
                while not stop.is_set():
                    async with sessions() as db:
                        connections = (
                            await db.execute(
                                text(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE datname=current_database() "
                                    "AND backend_type='client backend'"
                                )
                            )
                        ).scalar_one()
                        locks = (
                            await db.execute(
                                text(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE datname=current_database() "
                                    "AND wait_event_type='Lock'"
                                )
                            )
                        ).scalar_one()
                        pending = await db.scalar(
                            select(func.count())
                            .select_from(BackgroundJob)
                            .where(
                                BackgroundJob.desired_generation
                                > BackgroundJob.confirmed_generation
                            )
                        )
                        running = dict(
                            (
                                await db.execute(
                                    select(BackgroundJob.workload_class, func.count())
                                    .where(BackgroundJob.state == "running")
                                    .group_by(BackgroundJob.workload_class)
                                )
                            ).all()
                        )
                        active_jobs = list(
                            await db.scalars(
                                select(BackgroundJob).where(
                                    BackgroundJob.state == "running"
                                )
                            )
                        )
                        for job in active_jobs:
                            identity = (job.id, job.execution_token)
                            value = job.execution_deadline
                            assert deadlines.setdefault(identity, value) == value, (
                                "hard deadline renewed"
                            )
                            expected = 120 if job.job_kind == "diagnostic_turn" else 60
                            duration = (
                                value - job.execution_started_at
                            ).total_seconds()
                            assert abs(duration - expected) < 0.001, (
                                "wrong hard deadline"
                            )
                    sample = dict(
                        time=time.time(),
                        connections=connections,
                        lock_waiters=locks,
                        publication_pending=pending,
                        running=running,
                    )
                    try:
                        consumers = {}
                        for workload, durable in (
                            ("diagnostic", settings.nats_diagnostic_consumer),
                            ("profile_inference", settings.nats_profile_consumer),
                        ):
                            info = await js.consumer_info(
                                settings.nats_work_stream, durable
                            )
                            consumers[workload] = dict(
                                pending=info.num_pending,
                                ack_pending=info.num_ack_pending,
                                redelivered=info.num_redelivered,
                                filter=info.config.filter_subject,
                                max_ack_pending=info.config.max_ack_pending,
                                ack_wait=info.config.ack_wait,
                            )
                        stream = await js.stream_info(settings.nats_work_stream)
                        sample.update(consumers=consumers, stored=stream.state.messages)
                    except Exception:
                        sample["broker_unavailable"] = True
                    samples.append(sample)
                    await asyncio.sleep(1)

            monitoring = asyncio.create_task(monitor())

            async def request(path: str, **kwargs: Any) -> Any:
                response = await http.post(path, **kwargs)
                assert response.is_success, (
                    f"HTTP acceptance/setup status {response.status_code}"
                )
                return response.json()

            async def visible(
                model: type[BikeProfile] | type[RepairSession] | type[ArtifactRef],
                identity: str,
            ) -> None:
                # Fixture provisioning endpoints finalize their dependency's
                # transaction after sending the response. Fence setup against
                # cross-replica read races; never retry or fence timed turns.
                async with asyncio.timeout(5):
                    while True:
                        async with sessions() as db:
                            if await db.get(model, identity) is not None:
                                return
                        await asyncio.sleep(0.01)

            # Setup stays outside the traffic clock. Real artifact storage and
            # product API writes are used, with sentinel filenames and content.
            image = io.BytesIO()
            Image.new("RGB", (16, 16), color="red").save(image, format="JPEG")
            repairs = []
            for index in range(count):
                bike = await request("/bikes", json={"display_name": CONTENT})
                await visible(BikeProfile, bike["id"])
                repair = await request(
                    "/repair-sessions",
                    json={
                        "bike_id": bike["id"],
                        "client_session_id": f"capacity-{index}",
                    },
                )
                await visible(RepairSession, repair["id"])
                has_image = (
                    scenario in {"profile_backlog", "profile_failure"} or index % 2 == 0
                )
                artifact_ids = []
                if has_image:
                    artifact = await request(
                        "/artifacts",
                        data={
                            "purpose": "diagnostic_photo",
                            "repair_session_id": repair["id"],
                        },
                        files={
                            "file": (
                                f"{PATH_SENTINEL}.jpg",
                                image.getvalue(),
                                "image/jpeg",
                            )
                        },
                    )
                    artifact_ids = [artifact["artifact"]["id"]]
                    await visible(ArtifactRef, artifact_ids[0])
                repairs.append((repair["id"], artifact_ids))

            async def reader(
                repair_id: str, ready: asyncio.Event, completed: asyncio.Event
            ) -> None:
                cursor = "0"
                async with asyncio.timeout(330):
                    while True:
                        try:
                            async with http.stream(
                                "GET",
                                f"/repair-sessions/{repair_id}/events",
                                params={"after": cursor, "timeout_seconds": 120},
                                # Heartbeats arrive every 15s plus commit/network
                                # latency. Keep request deadlines at 15s, but
                                # give long-lived readers heartbeat margin.
                                timeout=httpx.Timeout(15, read=45),
                            ) as response:
                                if response.status_code >= 500:
                                    await asyncio.sleep(0.2)
                                    continue
                                assert response.status_code == 200
                                ready.set()
                                async for line in response.aiter_lines():
                                    if line.startswith("data: "):
                                        event = json.loads(line[6:])
                                        cursor = event["id"]
                                        if event["type"] == "heartbeat":
                                            continue
                                        created = datetime.fromisoformat(
                                            event["created_at"]
                                        )
                                        latency = (
                                            datetime.now(UTC) - created
                                        ).total_seconds()
                                        sse_latencies.append(latency)
                                        if (
                                            resumed_after is not None
                                            and created.timestamp() >= resumed_after
                                        ):
                                            resumed_sse_latencies.append(latency)
                                        if event["type"] == "turn.completed":
                                            completed.set()
                        except (httpx.TransportError, httpx.HTTPStatusError):
                            await asyncio.sleep(0.2)

            watched = (
                repairs[-8:]
                if scenario in {"profile_backlog", "profile_failure"}
                else repairs[:8]
            )
            for repair_id, _ in watched:
                ready = asyncio.Event()
                completed = asyncio.Event()
                read_ready.append(ready)
                read_completed.append(completed)
                readers.append(asyncio.create_task(reader(repair_id, ready, completed)))
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in read_ready)), 20)
            await asyncio.sleep(0.2)
            traffic_start = time.time()
            if scenario == "nats_outage":
                await action("offline")

            gate = asyncio.Semaphore(2)

            async def accept(index: int, repair: tuple[str, list[str]]) -> None:
                if scenario not in {"burst", "worker_loss", "broker_restart", "timing"}:
                    arrival = index % 24 if scenario == "nats_outage" else index
                    await asyncio.sleep(arrival * ARRIVAL_INTERVAL_SECONDS)
                async with gate:
                    start = time.monotonic()
                    await request(
                        f"/repair-sessions/{repair[0]}/turns",
                        json={
                            "schema_version": "ai_turn.v1",
                            "client_turn_id": f"turn-{index}",
                            "message": {"text": CONTENT, "artifact_ids": repair[1]},
                        },
                    )
                    latency = time.monotonic() - start
                    acceptance.append(latency)
                    if scenario == "nats_outage":
                        phase = outage_acceptance if index < 24 else restored_acceptance
                        phase.append(latency)

            try:
                initial = repairs[:24] if scenario == "nats_outage" else repairs
                await asyncio.gather(
                    *(accept(i, repair) for i, repair in enumerate(initial))
                )
                if scenario == "profile_backlog":
                    # Sustain provider saturation throughout the timed arrivals,
                    # then release the fake so finite drain measures recovery.
                    released = dict(options, profile_delay=0.25)
                    next_control = ROOT / "control-next.json"
                    next_control.write_text(json.dumps(released))
                    next_control.replace(ROOT / "control.json")
                if scenario == "nats_outage":
                    async with sessions() as db:
                        rows = list(await db.scalars(select(BackgroundJob)))
                        outage_intents = len(initial) + sum(bool(a) for _, a in initial)
                        assert len(rows) == outage_intents
                        assert all(row.confirmed_generation == 0 for row in rows)
                        outage_pending = sum(
                            row.desired_generation > row.confirmed_generation
                            for row in rows
                        )
                        assert outage_pending == outage_intents
                    await asyncio.sleep(3)
                    recovery_start = time.monotonic()
                    await action("restore")
                    await asyncio.gather(
                        *(
                            accept(i, repair)
                            for i, repair in enumerate(repairs[24:], start=24)
                        )
                    )
                elif scenario in {"worker_loss", "broker_restart"}:
                    async with asyncio.timeout(30):
                        while True:
                            async with sessions() as db:
                                active = list(
                                    await db.scalars(
                                        select(BackgroundJob).where(
                                            BackgroundJob.effect_boundary_at.is_not(
                                                None
                                            ),
                                            BackgroundJob.state == "running",
                                        )
                                    )
                                )
                            if active:
                                break
                            await asyncio.sleep(0.1)
                    recovery_start = time.monotonic()
                    await action("loss" if scenario == "worker_loss" else "restart")
                    # New acceptance after the API restart uses the same topology.
                    fresh_bike = await request("/bikes", json={"display_name": CONTENT})
                    await visible(BikeProfile, fresh_bike["id"])
                    fresh_repair = await request(
                        "/repair-sessions", json={"bike_id": fresh_bike["id"]}
                    )
                    await visible(RepairSession, fresh_repair["id"])
                    repairs.append((fresh_repair["id"], []))
                    if scenario == "worker_loss":
                        # Measure live delivery after all API hosts recover,
                        # separately from the age of replayed outage events.
                        # Keep eight readers, and require the replaced session's
                        # completion before moving its reader to the fresh turn.
                        await asyncio.wait_for(read_completed[0].wait(), 40)
                        readers[0].cancel()
                        await asyncio.gather(readers[0], return_exceptions=True)
                        ready = asyncio.Event()
                        completed = asyncio.Event()
                        resumed_after = time.time()
                        read_completed[0] = completed
                        readers[0] = asyncio.create_task(
                            reader(fresh_repair["id"], ready, completed)
                        )
                        await asyncio.wait_for(ready.wait(), 20)
                    await accept(count, repairs[-1])
                    count += 1
                else:
                    recovery_start = time.monotonic()

                if scenario == "duplicates":
                    async with sessions() as db:
                        jobs = list(await db.scalars(select(BackgroundJob)))
                    for _ in range(3):
                        for job in jobs:
                            await js.publish(
                                settings.nats_diagnostic_subject
                                if job.workload_class == "diagnostic"
                                else settings.nats_profile_subject,
                                json.dumps(
                                    {
                                        "version": 1,
                                        "job_id": job.id,
                                        "publication_generation": (
                                            job.desired_generation
                                        ),
                                    }
                                ).encode(),
                            )

                async with asyncio.timeout(220):
                    while True:
                        async with sessions() as db:
                            jobs = list(await db.scalars(select(BackgroundJob)))
                        if all(
                            j.state in {"succeeded", "dead", "interrupted"}
                            and j.desired_generation == j.confirmed_generation
                            for j in jobs
                        ):
                            break
                        await asyncio.sleep(0.25)
                drain = time.monotonic() - recovery_start
                recoveries.append(drain)
                await asyncio.wait_for(
                    asyncio.gather(*(event.wait() for event in read_completed)), 40
                )
                for task in readers:
                    task.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                # Allow terminal duplicate no-ops/ACKs and OTLP export to settle.
                transport_start = time.monotonic()
                async with asyncio.timeout(
                    scenario_budgets(scenario)["transport_drain_seconds"]
                ):
                    while True:
                        infos = [
                            await js.consumer_info(settings.nats_work_stream, name)
                            for name in (
                                settings.nats_diagnostic_consumer,
                                settings.nats_profile_consumer,
                            )
                        ]
                        if all(i.num_ack_pending == i.num_pending == 0 for i in infos):
                            break
                        await asyncio.sleep(0.2)
                transport_drain = time.monotonic() - transport_start
                await asyncio.sleep(2)
                async with sessions() as db:
                    for repair_id, _ in repairs:
                        completed = await db.scalar(
                            select(func.count())
                            .select_from(RepairSessionEvent)
                            .where(
                                RepairSessionEvent.repair_session_id == repair_id,
                                RepairSessionEvent.type == "turn.completed",
                            )
                        )
                        assert completed == 1, "missing or duplicate durable completion"
                    for job in jobs:
                        if job.job_kind == "diagnostic_turn":
                            allowed = (
                                {"succeeded", "interrupted"}
                                if scenario == "worker_loss"
                                else {"succeeded"}
                            )
                            assert job.state in allowed, (
                                "unexpected diagnostic failure: "
                                f"{job.state}/{job.latest_error_category} "
                                f"after {job.attempt_count} attempts"
                            )
                        elif scenario == "profile_failure":
                            assert (
                                job.state == "dead"
                                and job.attempt_count == job.attempt_limit
                            )
                        else:
                            assert job.state == "succeeded"
                    runs = list(await db.scalars(select(ProfileInferenceRun)))
                    claims = list(await db.scalars(select(BikeFactClaim)))
                    assert len(runs) == sum(bool(a) for _, a in repairs)
                    assert len(claims) == (
                        0 if scenario == "profile_failure" else len(runs)
                    )
                    bikes = list(await db.scalars(select(BikeProfile)))
                    assert all(b.profile_revision <= 1 for b in bikes)
                assert not invalid_messages, "invalid broker envelope or headers"
                async with sessions() as db:
                    private_identities = list(await db.scalars(select(User.id)))
                    private_identities += list(
                        await db.scalars(select(ArtifactRef.storage_path))
                    )
                # Exact deadlines remain policy-sized while progress protects
                # broker delivery. The database trigger also rejects renewal.
                timing = {}
                for kind in ("diagnostic_turn", "profile_inference"):
                    subset = [j for j in jobs if j.job_kind == kind]
                    timing[kind] = dict(
                        jobs=len(subset),
                        max_attempts=max(j.attempt_count for j in subset),
                        max_elapsed_seconds=max(
                            (j.terminal_at - j.first_started_at).total_seconds()
                            for j in subset
                        ),
                    )
                result = dict(
                    schema_version=1,
                    private_identities=private_identities,
                    scenario=scenario,
                    traffic_start=traffic_start,
                    finished=time.time(),
                    accepted_turns=count,
                    jobs=len(jobs),
                    acceptance_p95_seconds=percentile(acceptance),
                    outage_acceptance_p95_seconds=(
                        percentile(outage_acceptance)
                        if scenario == "nats_outage"
                        else None
                    ),
                    restored_acceptance_p95_seconds=(
                        percentile(restored_acceptance)
                        if scenario == "nats_outage"
                        else None
                    ),
                    sse_p95_seconds=percentile(sse_latencies),
                    sse_samples=len(sse_latencies),
                    sse_resumed_p95_seconds=(
                        percentile(resumed_sse_latencies)
                        if scenario == "worker_loss"
                        else None
                    ),
                    sse_resumed_samples=len(resumed_sse_latencies),
                    recovery_seconds=max(recoveries),
                    transport_drain_seconds=transport_drain,
                    transport_final={
                        workload: dict(
                            pending=info.num_pending, ack_pending=info.num_ack_pending
                        )
                        for workload, info in zip(
                            ("diagnostic", "profile_inference"), infos, strict=True
                        )
                    },
                    timing=timing,
                    database_connections_max=max(s["connections"] for s in samples),
                    lock_waiters_max=max(s["lock_waiters"] for s in samples),
                    outage_intents=outage_intents,
                    outage_publication_pending=outage_pending,
                    publication_pending_max=max(
                        outage_pending,
                        max(s["publication_pending"] for s in samples),
                    ),
                    database_samples=samples,
                    broker_samples=len(observed_messages),
                    durable_effects_unique=True,
                    interrupted_jobs=sum(j.state == "interrupted" for j in jobs),
                    fixed_deadlines_observed=len(deadlines),
                    fake_options=options,
                )
                (ROOT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                (ROOT / "broker.json").write_text(json.dumps(observed_messages) + "\n")
            finally:
                stop.set()
                await monitoring
                for task in readers:
                    task.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
    await engine.dispose()
