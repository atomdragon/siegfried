"""Benchmark utilities and percentile calculation for Siegfried SLOs."""

import time
from typing import Callable, List, NamedTuple


class LatencyStats(NamedTuple):
    count: int
    min_ms: float
    max_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def calculate_latency_stats(durations_ms: List[float]) -> LatencyStats:
    """Calculate P50, P95, P99 and basic statistics from measured durations in milliseconds."""
    if not durations_ms:
        return LatencyStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    sorted_d = sorted(durations_ms)
    count = len(sorted_d)

    def percentile(p: float) -> float:
        idx = int(p * (count - 1))
        return sorted_d[idx]

    return LatencyStats(
        count=count,
        min_ms=sorted_d[0],
        max_ms=sorted_d[-1],
        mean_ms=sum(sorted_d) / count,
        p50_ms=percentile(0.50),
        p95_ms=percentile(0.95),
        p99_ms=percentile(0.99),
    )


def measure_execution_time_ms(fn: Callable[[], None], iterations: int = 100) -> LatencyStats:
    """Run callable multiple times and return latency statistics in ms."""
    durations: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        durations.append((t1 - t0) * 1000.0)
    return calculate_latency_stats(durations)
