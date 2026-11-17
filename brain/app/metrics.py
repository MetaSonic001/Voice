"""Prometheus metrics (exposed at /metrics)."""

from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("brain_http_requests_total", "HTTP requests", ["method", "route", "status"])
REQUEST_SECONDS = Histogram("brain_http_request_seconds", "HTTP request latency", ["route"],
                            buckets=(0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 30))
LIVE_CALLS = Gauge("brain_live_calls", "Live voice calls in this process right now")
CALLS_REFUSED = Counter("brain_calls_refused_total", "Live calls refused because this process was at its limit")
TURNS = Counter("brain_turns_total", "Answered turns by outcome", ["outcome", "language"])
STAGE_SECONDS = Histogram("brain_stage_seconds", "Latency of each pipeline stage", ["stage"],
                          buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5))


def observe_turn(outcome: str, language: str, timings: dict) -> None:
    TURNS.labels(outcome, language).inc()
    for stage, ms in timings.items():
        if isinstance(ms, (int, float)):
            STAGE_SECONDS.labels(stage.removesuffix("_ms")).observe(ms / 1000)
