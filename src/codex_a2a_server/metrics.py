from __future__ import annotations

from threading import Lock

A2A_STREAM_REQUESTS_TOTAL = "a2a_stream_requests_total"
A2A_STREAM_ACTIVE = "a2a_stream_active"
CODEX_STREAM_RETRIES_TOTAL = "codex_stream_retries_total"
TOOL_CALL_CHUNKS_EMITTED_TOTAL = "tool_call_chunks_emitted_total"
INTERRUPT_REQUESTS_TOTAL = "interrupt_requests_total"
INTERRUPT_RESOLVED_TOTAL = "interrupt_resolved_total"

_COUNTER_NAMES = (
    A2A_STREAM_REQUESTS_TOTAL,
    CODEX_STREAM_RETRIES_TOTAL,
    TOOL_CALL_CHUNKS_EMITTED_TOTAL,
    INTERRUPT_REQUESTS_TOTAL,
    INTERRUPT_RESOLVED_TOTAL,
)
_GAUGE_NAMES = (A2A_STREAM_ACTIVE,)


class InMemoryMetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters = {name: 0 for name in _COUNTER_NAMES}
        self._gauges = {name: 0 for name in _GAUGE_NAMES}

    def inc_counter(self, name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def inc_gauge(self, name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            self._gauges[name] = self._gauges.get(name, 0) + amount

    def dec_gauge(self, name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            current = self._gauges.get(name, 0)
            self._gauges[name] = max(0, current - amount)

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }

    def reset(self) -> None:
        with self._lock:
            self._counters = {name: 0 for name in _COUNTER_NAMES}
            self._gauges = {name: 0 for name in _GAUGE_NAMES}


_registry = InMemoryMetricsRegistry()


def get_metrics_registry() -> InMemoryMetricsRegistry:
    return _registry


def reset_metrics() -> None:
    _registry.reset()


def snapshot_metrics() -> dict[str, dict[str, int]]:
    return _registry.snapshot()
