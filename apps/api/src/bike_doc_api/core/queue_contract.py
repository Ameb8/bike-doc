"""Static queue telemetry vocabulary and cardinality contract."""

# Stable metric -> instrument/unit. Attributes are validated before any sink sees them.
CONTRACT = {
    "publication_pending": ("gauge", "{job}"),
    "publication_oldest": ("gauge", "s"),
    "publication_ack": ("histogram", "s"),
    "publication": ("counter", "{publication}"),
    "transport_messages": ("gauge", "{message}"),
    "queue_latency": ("histogram", "s"),
    "execution_duration": ("histogram", "s"),
    "outcome": ("counter", "{job}"),
    "resolution": ("counter", "{delivery}"),
    "active": ("updown", "{execution}"),
    "recovery": ("counter", "{job}"),
    "worker_available": ("gauge", "1"),
    "worker_health": ("counter", "{event}"),
    "advisory": ("counter", "{advisory}"),
    "sse_latency": ("histogram", "s"),
    "sse_recovery": ("counter", "{event}"),
    "pool": ("gauge", "{connection}"),
    "maintenance": ("counter", "{event}"),
}
DIMENSIONS = {
    "maintenance": frozenset(
        {
            "notification_repair",
            "cleanup_deleted",
            "cleanup_scanned",
            "publication_scan_failed",
            "reconciliation_scan_failed",
            "cleanup_scan_failed",
            "observation_failed",
        }
    ),
    "workload": frozenset({"diagnostic", "profile_inference"}),
    "kind": frozenset({"diagnostic_turn", "profile_inference"}),
    "version": frozenset({"1"}),
    "outcome": frozenset({"succeeded", "retrying", "dead", "interrupted"}),
    "publication": frozenset(
        {"confirmed", "duplicate", "unavailable", "timeout", "rejected"}
    ),
    "resolution": frozenset(
        {
            "executable",
            "terminal",
            "stale",
            "early",
            "running",
            "recovery_wait",
            "prohibited",
            "missing",
            "definition_failure",
            "exhausted",
            "stale_token",
        }
    ),
    "health": frozenset({"ready", "fetch_failed", "unsettled", "graceful", "forced"}),
    "advisory": frozenset({"max_deliver", "terminated"}),
    "consumer": frozenset({"diagnostic", "profile_inference", "work_stream"}),
    "transport": frozenset(
        {"stored", "pending", "redelivered", "ack_pending", "waiting"}
    ),
    "role": frozenset({"api", "worker", "sse", "maintenance", "adk"}),
    "pool": frozenset({"size", "checked_out", "overflow"}),
}
FIELDS = {
    "maintenance": frozenset({"maintenance"}),
    "publication_pending": frozenset(),
    "publication_oldest": frozenset(),
    "publication_ack": frozenset({"workload"}),
    "publication": frozenset({"workload", "publication"}),
    "transport_messages": frozenset({"consumer", "transport"}),
    "queue_latency": frozenset({"kind", "version", "workload"}),
    "execution_duration": frozenset({"kind", "version", "workload"}),
    "outcome": frozenset({"kind", "version", "workload", "outcome"}),
    "resolution": frozenset({"workload", "resolution"}),
    "active": frozenset({"workload"}),
    "recovery": frozenset({"kind", "outcome"}),
    "worker_available": frozenset({"workload"}),
    "worker_health": frozenset({"workload", "health"}),
    "advisory": frozenset({"consumer", "advisory"}),
    "sse_latency": frozenset(),
    "sse_recovery": frozenset(),
    "pool": frozenset({"role", "pool"}),
}
