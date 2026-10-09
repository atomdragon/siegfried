#!/usr/bin/env python3
"""Benchmark harness for validating Siegfried Architecture SLOs.

Target SLOs from SPECIFICATION.md:
- Fast-Path Regex router: P95 < 10 ms
- CLI Cold-Start: P95 < 50 ms
- Vault Event Append (with fcntl + fsync on persistent disk): P95 < 10 ms
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Add src to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from siegfried.cli.router import CommandRouter
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault
from siegfried.contracts.events import Event, EventType
from siegfried.observability.benchmarks import calculate_latency_stats


def benchmark_fast_path_router() -> bool:
    router = CommandRouter()
    queries = [
        "iniciar bloque de 50 para arquitectura",
        "status",
        "cancelar",
        "posponer 10",
        "silencio",
    ]

    durations: list[float] = []
    iterations = 500
    for _ in range(iterations):
        for q in queries:
            t0 = time.perf_counter()
            match = router.route(q)
            t1 = time.perf_counter()
            durations.append((t1 - t0) * 1000.0)

    stats = calculate_latency_stats(durations)
    slo = 10.0
    passed = stats.p95_ms < slo
    verdict = "PASS" if passed else "FAIL"
    print(f"\n[SLO Fast-Path Router (P95 < {slo} ms)]")
    print(f"  Count: {stats.count} | Mean: {stats.mean_ms:.4f} ms | P50: {stats.p50_ms:.4f} ms | P95: {stats.p95_ms:.4f} ms | Max: {stats.max_ms:.4f} ms")
    print(f"  Verdict: {verdict}")
    return passed


def benchmark_cli_cold_start() -> bool:
    bin_cli = REPO_ROOT / "bin" / "siegfried"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)

    durations: list[float] = []
    iterations = 50
    for _ in range(iterations):
        t0 = time.perf_counter()
        subprocess.run(
            [sys.executable, str(bin_cli), "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            check=True
        )
        t1 = time.perf_counter()
        durations.append((t1 - t0) * 1000.0)

    stats = calculate_latency_stats(durations)
    slo = 50.0
    passed = stats.p95_ms < slo
    verdict = "PASS" if passed else "FAIL"
    print(f"\n[SLO CLI Cold-Start (P95 < {slo} ms)]")
    print(f"  Count: {stats.count} | Mean: {stats.mean_ms:.2f} ms | P50: {stats.p50_ms:.2f} ms | P95: {stats.p95_ms:.2f} ms | Max: {stats.max_ms:.2f} ms")
    print(f"  Verdict: {verdict}")
    return passed


def benchmark_vault_atomic_append() -> bool:
    # Use REPO_ROOT (persistent disk filesystem, ext4) instead of /tmp (tmpfs) for real fsync measurement
    with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
        paths = SiegfriedPaths(base_dir=Path(tmp_dir), runtime_dir=Path(tmp_dir) / "run")
        paths.ensure_directories()
        vault = Vault(paths.vault_file)

        event = Event.create(EventType.POMODORO_COMPLETED, {"duration_min": 50, "course": "Siegfried Core"})

        durations: list[float] = []
        iterations = 200
        for _ in range(iterations):
            t0 = time.perf_counter()
            vault.append(event)
            t1 = time.perf_counter()
            durations.append((t1 - t0) * 1000.0)

        stats = calculate_latency_stats(durations)
        slo = 10.0
        passed = stats.p95_ms < slo
        verdict = "PASS" if passed else "FAIL"
        print(f"\n[SLO Vault Append (fcntl + fsync on persistent disk)]")
        print(f"  Filesystem: ext4 (dir={REPO_ROOT})")
        print(f"  Count: {stats.count} | Mean: {stats.mean_ms:.4f} ms | P50: {stats.p50_ms:.4f} ms | P95: {stats.p95_ms:.4f} ms | Max: {stats.max_ms:.4f} ms")
        print(f"  Verdict: {verdict}")
        return passed


def benchmark_orchestrator_decision() -> bool:
    from siegfried.inference.orchestrator import InferenceOrchestrator
    from siegfried.contracts.inference import (
        InferenceMessage,
        InferencePolicy,
        InferenceRequest,
        InferenceResponse,
    )

    class FastStubEngine:
        def generate(self, req: InferenceRequest) -> InferenceResponse:
            return InferenceResponse(content="ok", model="stub", raw_status=200)

    cloud_stub = FastStubEngine()
    local_stub = FastStubEngine()
    orchestrator = InferenceOrchestrator(cloud_client=cloud_stub, local_client=local_stub)
    req = InferenceRequest(
        messages=[InferenceMessage(role="user", content="ping")],
        model="deepseek-chat",
    )

    durations: list[float] = []
    iterations = 1000
    for _ in range(iterations):
        t0 = time.perf_counter()
        orchestrator.orchestrate(req, policy=InferencePolicy.CLOUD_PREFERRED)
        t1 = time.perf_counter()
        durations.append((t1 - t0) * 1000.0)

    stats = calculate_latency_stats(durations)
    slo = 1.0
    passed = stats.p95_ms < slo
    verdict = "PASS" if passed else "FAIL"
    print(f"\n[Microbenchmark Orchestrator Routing (P95 < {slo:.1f} ms)]")
    print(f"  Count: {stats.count} | Mean: {stats.mean_ms:.4f} ms | P50: {stats.p50_ms:.4f} ms | P95: {stats.p95_ms:.4f} ms | Max: {stats.max_ms:.4f} ms")
    print(f"  Verdict: {verdict}")
    return passed


def benchmark_historical_aggregator() -> bool:
    import json
    from siegfried.storage.aggregator import HistoricalAggregator

    event_types = [
        EventType.POMODORO_COMPLETED,
        EventType.BREAK_COMPLETED,
        EventType.POSTURE_WARNING,
        EventType.POSTPONE_GRANTED,
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        vault_path = Path(tmp_dir) / "siegfried_vault.jsonl"

        # Populate 20,000 synthetic Event Schema v1 events
        total_events = 20000
        now = 1700000000.0
        start_time = now - (total_events * 120.0)
        lines = []
        for i in range(total_events):
            ts = start_time + (i * 120.0)
            etype = event_types[i % len(event_types)]
            data = {"duration_min": 25, "task": f"Task-{i % 5}"} if etype == EventType.POMODORO_COMPLETED else {}
            ev = Event.create(etype, data, ts=ts)
            lines.append(json.dumps(ev.to_dict()) + "\n")

        with open(vault_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        aggregator = HistoricalAggregator(vault_path=vault_path)

        # Query recent window (last 24 hours: 720 events)
        window_start = now - 86400.0
        window_end = now

        # 1. Heuristic Early-Exit Mode (SLO P95 < 10.0 ms)
        durations_heuristic: list[float] = []
        iterations = 100
        for _ in range(iterations):
            t0 = time.perf_counter()
            metrics = aggregator.aggregate(start_ts=window_start, end_ts=window_end, allow_early_exit=True)
            _ = aggregator.format_prompt_block(metrics)
            t1 = time.perf_counter()
            durations_heuristic.append((t1 - t0) * 1000.0)

        stats_heuristic = calculate_latency_stats(durations_heuristic)
        slo = 10.0
        passed_heuristic = stats_heuristic.p95_ms < slo
        verdict_heuristic = "PASS" if passed_heuristic else "FAIL"
        print(f"\n[SLO Historical Aggregator — Modo Heurístico Early-Exit (P95 < {slo:.1f} ms para 20k eventos)]")
        print(f"  Count: {stats_heuristic.count} | Mean: {stats_heuristic.mean_ms:.4f} ms | P50: {stats_heuristic.p50_ms:.4f} ms | P95: {stats_heuristic.p95_ms:.4f} ms | Max: {stats_heuristic.max_ms:.4f} ms")
        print(f"  Verdict: {verdict_heuristic}")

        # 2. Exhaustive scan latency; correctness covered separately by tests.
        durations_exhaustive: list[float] = []
        for _ in range(25):
            t0 = time.perf_counter()
            metrics_ex = aggregator.aggregate(start_ts=window_start, end_ts=window_end, allow_early_exit=False)
            _ = aggregator.format_prompt_block(metrics_ex)
            t1 = time.perf_counter()
            durations_exhaustive.append((t1 - t0) * 1000.0)

        stats_exhaustive = calculate_latency_stats(durations_exhaustive)
        print(f"\n[Historical Aggregator — Modo Exhaustivo Exacto (20k eventos sintéticos, 25 muestras)]")
        print(f"  Count: {stats_exhaustive.count} | Mean: {stats_exhaustive.mean_ms:.4f} ms | P50: {stats_exhaustive.p50_ms:.4f} ms | P95: {stats_exhaustive.p95_ms:.4f} ms | Max: {stats_exhaustive.max_ms:.4f} ms")
        print(f"  Nota Técnica: Escaneo y formato de 20k eventos separados 120 s (~27.8 días), caché caliente; sin garantía de latencia o exactitud universal.")

        return passed_heuristic


def main() -> int:
    print("=== Ejecutando Benchmark Harness de Siegfried ===")
    r_router = benchmark_fast_path_router()
    r_vault = benchmark_vault_atomic_append()
    r_cli = benchmark_cli_cold_start()
    r_orch = benchmark_orchestrator_decision()
    r_agg = benchmark_historical_aggregator()

    all_passed = r_router and r_vault and r_cli and r_orch and r_agg
    if all_passed:
        print("\n=== Todos los benchmarks cumplieron sus SLOs (PASS) ===")
        return 0
    else:
        print("\n=== Uno o más benchmarks no alcanzaron el umbral estricto de SLO (FAIL) ===")
        return 1


if __name__ == "__main__":
    sys.exit(main())
