"""Observability package exposing structured logging and benchmark utilities."""

from siegfried.observability.logging import setup_logger, StructuredFormatter
from siegfried.observability.benchmarks import calculate_latency_stats, measure_execution_time_ms, LatencyStats

__all__ = [
    "setup_logger",
    "StructuredFormatter",
    "calculate_latency_stats",
    "measure_execution_time_ms",
    "LatencyStats",
]
