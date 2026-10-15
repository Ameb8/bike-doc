"""The observation boundary rejects private/unbounded data before any sink."""

import asyncio
import json
from itertools import product
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from bike_doc_api.core.config import Settings
from bike_doc_api.core.queue_contract import CONTRACT, DIMENSIONS, FIELDS
from bike_doc_api.core.queue_telemetry import QueueTelemetry, RecordingQueueTelemetry
from bike_doc_api.operations import inspection
from bike_doc_api.operations.monitoring import QueueMonitor
from bike_doc_api.repositories.job_inspection import (
    COLUMNS,
    InspectionFilter,
    JobInspectionRepository,
)

SENTINEL = (
    "private-user@example.org nats://name:password@host prompt /artifacts/private"
)


def test_all_contract_series_have_exact_bounded_dimensions() -> None:
    recorder = RecordingQueueTelemetry()
    for metric in CONTRACT:
        keys = sorted(FIELDS[metric])
        for values in product(*(DIMENSIONS[key] for key in keys)):
            attrs = dict(zip(keys, values, strict=True))
            recorder.observe(metric, 1, **attrs)
            assert recorder.observations[-1].attributes == attrs
            before = len(recorder.observations)
            recorder.observe(metric, 1, job_id=SENTINEL, **attrs)
            for key in keys:
                recorder.observe(metric, 1, **{**attrs, key: SENTINEL})
            assert len(recorder.observations) == before
    assert SENTINEL not in repr(recorder.observations)
    # No dynamic dimensions or arbitrary meter names can enter the exporter.
    recorder.observe(SENTINEL, 1)
    recorder.observe("sse_latency", float("nan"))
    assert SENTINEL not in repr(recorder.observations)


def test_export_and_log_failures_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object) -> None:
        raise RuntimeError(SENTINEL)

    telemetry = QueueTelemetry(fail)
    telemetry.observe("sse_recovery")
    telemetry.observe("active", -1, workload="diagnostic")


@pytest.mark.parametrize(
    "values",
    [
        {"limit": 0},
        {"limit": 201},
        {"minimum_age_seconds": -1},
        {"kind": SENTINEL},
        {"state": SENTINEL},
        {"after": SENTINEL},
        {"input": SENTINEL},
        {"version": 0},
        {"workload": SENTINEL},
    ],
)
def test_invalid_filters_never_reach_database(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InspectionFilter.model_validate(values)


async def test_projection_excludes_inputs_tokens_and_domain_joins() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    session.execute.return_value = result
    assert await JobInspectionRepository(session).page(
        InspectionFilter(
            limit=2,
            terminal=False,
            kind="profile_inference",
            workload="profile_inference",
            state="queued",
            version=1,
            minimum_age_seconds=60,
        )
    ) == {"jobs": [], "next_after": None}
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    selected = {c.name for c in statement.selected_columns}
    assert selected == set(COLUMNS)
    assert not selected & {
        "input",
        "deduplication_key",
        "execution_token",
        "effect_boundary_token",
        "publication_claim_token",
    }
    assert "JOIN" not in sql and "FOR UPDATE" not in sql
    assert "ORDER BY background_jobs.id" in sql
    assert "LIMIT" in sql and "created_at <=" in sql


async def test_pagination_and_unregistered_definitions_are_safe() -> None:
    session = AsyncMock()
    result = MagicMock()
    rows = [
        {
            "id": str(index),
            "job_kind": "private_content",
            "workload_class": "private_content",
        }
        for index in range(3)
    ]
    result.mappings.return_value.all.return_value = rows
    session.execute.return_value = result
    page = await JobInspectionRepository(session).page(InspectionFilter(limit=2))
    assert page == {
        "jobs": [
            {"id": "0", "job_kind": "unregistered", "workload_class": "unregistered"},
            {"id": "1", "job_kind": "unregistered", "workload_class": "unregistered"},
        ],
        "next_after": "1",
    }


def test_advisory_adapter_only_emits_allowlisted_observations() -> None:
    settings = Settings(environment="test")
    recorder = RecordingQueueTelemetry()
    monitor = QueueMonitor(settings, recorder)
    for suffix in ("MAX_DELIVERIES", "MSG_TERMINATED"):
        subject = (
            f"$JS.EVENT.ADVISORY.CONSUMER.{suffix}."
            f"{settings.nats_work_stream}.{settings.nats_profile_consumer}"
        )
        body = json.dumps(
            {
                "stream": settings.nats_work_stream,
                "consumer": settings.nats_profile_consumer,
                "reason": SENTINEL,
                "id": SENTINEL,
            }
        ).encode()
        monitor.advisory(subject, body)
        monitor.advisory(subject + SENTINEL, body)
        monitor.advisory(subject, b"invalid")
        monitor.advisory(subject, b"[]")
    assert [(o.metric, o.attributes) for o in recorder.observations] == [
        ("advisory", {"consumer": "profile_inference", "advisory": "max_deliver"}),
        ("advisory", {"consumer": "profile_inference", "advisory": "terminated"}),
    ]
    assert SENTINEL not in repr(recorder.observations)
    assert not hasattr(monitor, "repository")


def test_command_malformed_filters_do_not_echo_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["inspection", "--filters", json.dumps({"input": SENTINEL})]
    )
    with pytest.raises(SystemExit) as exit:
        inspection.main()
    assert exit.value.code == 2
    output = capsys.readouterr()
    assert SENTINEL not in output.out + output.err
    assert "Invalid operational filters" in output.err


async def test_inspection_preserves_database_view_on_broker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.begin = MagicMock(return_value=AsyncMock())
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    engine = AsyncMock()
    monkeypatch.setattr(inspection, "create_async_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(inspection, "async_sessionmaker", lambda *a: factory)
    monkeypatch.setattr(
        JobInspectionRepository,
        "page",
        AsyncMock(return_value={"jobs": [], "next_after": None}),
    )
    monkeypatch.setattr(
        QueueMonitor, "snapshot", AsyncMock(side_effect=RuntimeError(SENTINEL))
    )
    result = await inspection.inspect_jobs(
        InspectionFilter(), transport=True, settings=Settings(environment="test")
    )
    assert result == {
        "postgresql_truth": {"jobs": [], "next_after": None},
        "jetstream_transport_observations": {"status": "unavailable"},
    }
    assert str(session.execute.call_args.args[0]) == "SET TRANSACTION READ ONLY"
    session.commit.assert_not_called()
    engine.dispose.assert_awaited_once()
    assert SENTINEL not in repr(result)


async def test_inspection_cancellation_disposes_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.begin = MagicMock(return_value=AsyncMock())
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    engine = AsyncMock()
    monkeypatch.setattr(inspection, "create_async_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(inspection, "async_sessionmaker", lambda *a: factory)
    monkeypatch.setattr(
        JobInspectionRepository, "page", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await inspection.inspect_jobs(
            InspectionFilter(), settings=Settings(environment="test")
        )
    engine.dispose.assert_awaited_once()
    session.commit.assert_not_called()


def test_real_otel_export_and_structured_events_exclude_private_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from structlog.testing import capture_logs

    from bike_doc_api.core import queue_telemetry as module

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    spans = InMemorySpanExporter()
    tracing = TracerProvider()
    tracing.add_span_processor(SimpleSpanProcessor(spans))
    monkeypatch.setattr(
        module.metrics, "get_meter", lambda name: provider.get_meter(name)
    )
    telemetry = QueueTelemetry()
    with (
        capture_logs() as logs,
        tracing.get_tracer("test").start_as_current_span("queue_test"),
    ):
        for metric, fields in FIELDS.items():
            attrs = {key: sorted(DIMENSIONS[key])[0] for key in fields}
            telemetry.observe(metric, 1, **attrs)
            telemetry.observe(metric, 1, secret=SENTINEL, **attrs)
    data = reader.get_metrics_data()
    assert data is not None
    names = {
        m.name
        for r in data.resource_metrics
        for s in r.scope_metrics
        for m in s.metrics
    }
    assert names == {f"bike_doc.queue.{name}" for name in CONTRACT}
    assert SENTINEL not in repr(data) + repr(logs) + repr(
        [dict(span.attributes or {}) for span in spans.get_finished_spans()]
    )
    assert all(
        set(event) <= {"event", "log_level", "signal"} | set(DIMENSIONS)
        for event in logs
    )
    provider.shutdown()
    tracing.shutdown()
