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


def main() -> int:
    print("=== Ejecutando Benchmark Harness de Siegfried ===")
    r_router = benchmark_fast_path_router()
    r_vault = benchmark_vault_atomic_append()
    r_cli = benchmark_cli_cold_start()
    r_orch = benchmark_orchestrator_decision()
    
    all_passed = r_router and r_vault and r_cli and r_orch
    if all_passed:
        print("\n=== Todos los benchmarks cumplieron sus SLOs (PASS) ===")
        return 0
    else:
        print("\n=== Uno o más benchmarks no alcanzaron el umbral estricto de SLO (FAIL) ===")
        return 1


if __name__ == "__main__":
    sys.exit(main())
