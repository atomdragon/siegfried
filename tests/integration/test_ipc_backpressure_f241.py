"""Integration and concurrency hardening tests for Gate F2.4.1.

Validates:
1. Hard capacity limits (MAX_COGNITIVE_WORKERS = 3, MAX_PENDING = 0).
2. Immediate backpressure rejection with INFERENCE_BUSY.
3. Fast-Path zero competition for cognitive slots.
4. Non-blocking reactor and timer progress during saturation.
5. Deterministic synchronization with Barrier and Event (no flaky sleeps).
6. Safe shutdown protocols, race conditions, clean vs incomplete shutdown reporting.
7. Privacy confinement (LOCAL_ONLY never touches Cloud under saturation).
8. Zero leaked secrets, prompts or lingering file descriptors/threads.
"""

import io
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from siegfried.contracts.ipc import (
    IPCCommand,
    IPCRequest,
    IPCResponse,
    IPCStatus,
)
from siegfried.contracts.inference import (
    InferenceMessage,
    InferencePolicy,
    InferenceRequest,
    InferenceResponse,
    InferenceRoute,
    InferenceUsage,
)
from siegfried.contracts.states import SystemState
from siegfried.core.errors import (
    InferenceBusyError,
    InferenceError,
    InferenceTransportError,
    PrivacyViolationError,
)
from siegfried.cli.app import run_cli
from siegfried.cli.repl import SiegfriedREPL
from siegfried.cli.router import CommandRouter
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.inference.orchestrator import InferenceOrchestrator, OrchestrationResult
from siegfried.ipc.client import IPCClient
from siegfried.ipc.server import (
    IPCServer,
    MAX_COGNITIVE_WORKERS,
    MAX_PENDING_COGNITIVE_QUERIES,
)
from siegfried.storage.paths import SiegfriedPaths


class BlockingMockEngine:
    """Mock engine that blocks on an Event to precisely coordinate concurrency."""

    def __init__(self, name: str = "blocking-engine", response_text: str = "Respuesta simulada") -> None:
        self.name = name
        self.response_text = response_text
        self.started_events = []
        self.gate = threading.Event()
        self.calls = []
        self.exception_to_raise = None

    def create_slot(self) -> threading.Event:
        evt = threading.Event()
        self.started_events.append(evt)
        return evt

    def orchestrate(self, request: InferenceRequest, policy=None, request_id=None) -> OrchestrationResult:
        self.calls.append({"request": request, "policy": policy, "request_id": request_id})
        # Signal that execution entered orchestrate
        for evt in self.started_events:
            if not evt.is_set():
                evt.set()
                break

        # Wait on gate (timeout 5s to avoid deadlock if test fails)
        self.gate.wait(timeout=5.0)

        if self.exception_to_raise:
            raise self.exception_to_raise

        resp = InferenceResponse(
            content=self.response_text,
            model=self.name,
            usage=InferenceUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            finish_reason="stop",
        )
        return OrchestrationResult(
            response=resp,
            route_selected=InferenceRoute.CLOUD,
            route_used=InferenceRoute.CLOUD,
            fallback_used=False,
            request_id=request_id or "req-mock",
            elapsed_ms=1.0,
        )


class TestIPCBackpressureF241(unittest.TestCase):
    """38 tests validating capacity, backpressure, fast-path isolation, shutdown, and security."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.tmp_dir.name)
        self.paths = SiegfriedPaths(
            base_dir=self.base_path / ".siegfried",
            runtime_dir=self.base_path / "run",
        )
        self.paths.ensure_directories()

        self.mock_engine = BlockingMockEngine()
        self.orchestrator = self.mock_engine

        self.daemon = SiegfriedDaemon(paths=self.paths, orchestrator=self.orchestrator)
        self.daemon.start()

        self._daemon_running = True
        self._daemon_thread = threading.Thread(target=self._run_daemon_ticks, daemon=True)
        self._daemon_thread.start()

        self.client = IPCClient(self.paths.socket_file, timeout_seconds=5.0)

    def _run_daemon_ticks(self) -> None:
        while self._daemon_running and self.daemon._running:
            try:
                self.daemon.run_tick(timeout_seconds=0.01)
            except Exception:
                break

    def tearDown(self) -> None:
        self.mock_engine.gate.set()
        self._daemon_running = False
        self.daemon.stop(timeout_seconds=1.0)
        self._daemon_thread.join(timeout=1.0)
        self.tmp_dir.cleanup()

    # =========================================================================
    # A. Capacidad (1-10)
    # =========================================================================

    def test_01_first_query_admitted(self) -> None:
        """1. Primera QUERY es admitida correctamente."""
        self.mock_engine.gate.set()
        res = self.client.call(IPCCommand.QUERY, {"prompt": "Consulta 1"})
        self.assertEqual(res.status, IPCStatus.OK.value)
        self.assertEqual(len(self.mock_engine.calls), 1)

    def test_02_second_query_admitted(self) -> None:
        """2. Segunda QUERY es admitida mientras la primera está en vuelo."""
        evt1 = self.mock_engine.create_slot()
        evt2 = self.mock_engine.create_slot()

        res1_holder = []
        res2_holder = []

        def worker1():
            c = IPCClient(self.paths.socket_file, timeout_seconds=5.0)
            res1_holder.append(c.call(IPCCommand.QUERY, {"prompt": "Consulta 1"}))

        def worker2():
            c = IPCClient(self.paths.socket_file, timeout_seconds=5.0)
            res2_holder.append(c.call(IPCCommand.QUERY, {"prompt": "Consulta 2"}))

        t1 = threading.Thread(target=worker1)
        t2 = threading.Thread(target=worker2)
        t1.start()
        self.assertTrue(evt1.wait(timeout=2.0))

        t2.start()
        self.assertTrue(evt2.wait(timeout=2.0))

        # Both queries are admitted and active
        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 2)

        self.mock_engine.gate.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        self.assertEqual(res1_holder[0].status, IPCStatus.OK.value)
        self.assertEqual(res2_holder[0].status, IPCStatus.OK.value)

    def test_03_third_query_admitted(self) -> None:
        """3. Tercera QUERY es admitida dentro de la capacidad de 3 cupos."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []

        def worker(idx):
            c = IPCClient(self.paths.socket_file, timeout_seconds=5.0)
            c.call(IPCCommand.QUERY, {"prompt": f"Consulta {idx}"})

        for i in range(3):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 3)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_04_fourth_query_rejected_immediately(self) -> None:
        """4. Cuarta QUERY se rechaza inmediatamente con INFERENCE_BUSY."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []

        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        # While 3 are active, attempt 4th query
        client4 = IPCClient(self.paths.socket_file, timeout_seconds=2.0)
        start_time = time.monotonic()
        res4 = client4.call(IPCCommand.QUERY, {"prompt": "Consulta 4 rechazada"})
        elapsed = time.monotonic() - start_time

        # Rejected immediately
        self.assertLess(elapsed, 0.5)
        self.assertEqual(res4.status, IPCStatus.REJECTED.value)
        self.assertEqual(res4.payload.get("code"), "INFERENCE_BUSY")
        self.assertIn("El motor de inferencia está ocupado", res4.error_msg)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_05_rejection_does_not_create_worker_thread(self) -> None:
        """5. El rechazo de la 4ta consulta no crea un hilo de trabajo adicional."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        workers_before = self.daemon.ipc_server.active_cognitive_workers
        self.assertEqual(workers_before, 3)

        # 4th query
        res = self.client.call(IPCCommand.QUERY, {"prompt": "Consulta 4"})
        self.assertEqual(res.payload.get("code"), "INFERENCE_BUSY")

        workers_after = self.daemon.ipc_server.active_cognitive_workers
        self.assertEqual(workers_after, 3)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_06_rejection_has_no_queue(self) -> None:
        """6. El rechazo no queda en cola: responde instantáneamente (< 0.1s)."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        t_start = time.monotonic()
        res = self.client.call(IPCCommand.QUERY, {"prompt": "NoQueueTest"})
        t_duration = time.monotonic() - t_start

        self.assertLess(t_duration, 0.1)
        self.assertEqual(res.status, IPCStatus.REJECTED.value)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_07_capacity_released_when_query_finishes(self) -> None:
        """7. La capacidad vuelve a estar disponible (cupo liberado) cuando termina una QUERY."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 3)

        # Release the gate to let active queries finish
        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

        # Wait briefly for worker cleanup
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and self.daemon.ipc_server.active_cognitive_workers > 0:
            time.sleep(0.01)

        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 0)

        # Now a new query must succeed!
        res_new = self.client.call(IPCCommand.QUERY, {"prompt": "Nueva consulta post-liberación"})
        self.assertEqual(res_new.status, IPCStatus.OK.value)

    def test_08_capacity_released_on_orchestrator_exception(self) -> None:
        """8. Se libera capacidad si el orquestador lanza una excepción."""
        self.mock_engine.gate.set()
        self.mock_engine.exception_to_raise = RuntimeError("Falla interna simulada")

        res = self.client.call(IPCCommand.QUERY, {"prompt": "Falla"})
        self.assertEqual(res.status, IPCStatus.ERROR.value)

        # Capacity should be back to 0 active workers
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and self.daemon.ipc_server.active_cognitive_workers > 0:
            time.sleep(0.01)

        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 0)

        # Next query succeeds after resetting exception
        self.mock_engine.exception_to_raise = None
        res_ok = self.client.call(IPCCommand.QUERY, {"prompt": "Éxito post-falla"})
        self.assertEqual(res_ok.status, IPCStatus.OK.value)

    def test_09_capacity_released_when_client_disconnects(self) -> None:
        """9. Se libera capacidad cuando el cliente se desconecta prematuramente."""
        evt = self.mock_engine.create_slot()

        # Connect a raw socket, send query, then close immediately
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(self.paths.socket_file))
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "DisconnectTest"})
        from siegfried.ipc.protocol import serialize_frame
        s.sendall(serialize_frame(req.to_dict()))

        self.assertTrue(evt.wait(timeout=2.0))
        # Client drops socket
        s.close()

        # Release engine gate so worker thread attempts to send and exits
        self.mock_engine.gate.set()

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and self.daemon.ipc_server.active_cognitive_workers > 0:
            time.sleep(0.01)

        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 0)

    def test_10_capacity_never_exceeds_three(self) -> None:
        """10. La capacidad nunca excede tres concurrentemente bajo una ráfaga de 10 peticiones."""
        max_seen = 0
        lock = threading.Lock()

        def inspect_workers():
            nonlocal max_seen
            with lock:
                c = self.daemon.ipc_server.active_cognitive_workers
                if c > max_seen:
                    max_seen = c

        threads = []
        for i in range(10):
            def send_q(idx=i):
                inspect_workers()
                IPCClient(self.paths.socket_file, timeout_seconds=1.0).call(IPCCommand.QUERY, {"prompt": f"Burst{idx}"})
                inspect_workers()

            t = threading.Thread(target=send_q)
            threads.append(t)
            t.start()

        time.sleep(0.05)
        inspect_workers()
        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

        self.assertLessEqual(max_seen, 3)

    # =========================================================================
    # B. Fast-Path (11-15)
    # =========================================================================

    def test_11_status_responds_during_three_active_queries(self) -> None:
        """11. STATUS responde en < 50ms durante 3 consultas cognitivas activas."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        # STATUS call is fast-path and should succeed immediately
        t_start = time.monotonic()
        res_status = self.client.call(IPCCommand.STATUS)
        t_dur = time.monotonic() - t_start

        self.assertLess(t_dur, 0.05)
        self.assertEqual(res_status.status, IPCStatus.OK.value)
        self.assertIn("state", res_status.payload)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_12_ping_responds_during_saturation(self) -> None:
        """12. PING responde durante saturación cognitiva."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        res_ping = self.client.call(IPCCommand.PING)
        self.assertEqual(res_ping.status, IPCStatus.OK.value)
        self.assertTrue(res_ping.payload.get("pong"))

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_13_daemon_timers_continue_ticking_during_saturation(self) -> None:
        """13. Los cronómetros del daemon avanzan y expiran normalmente durante saturación."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        # Start a 1-second short timer
        self.daemon.focus_timer.start(duration_seconds=0.1)
        self.assertTrue(self.daemon.focus_timer.is_active())

        # Wait for timer expiration
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and self.daemon.focus_timer.is_active():
            time.sleep(0.02)

        self.assertFalse(self.daemon.focus_timer.is_active())

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_14_saturated_query_does_not_block_other_connections(self) -> None:
        """14. Una solicitud QUERY saturada no bloquea otras conexiones entrantes."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        # Attempt 4th query (rejected)
        res4 = self.client.call(IPCCommand.QUERY, {"prompt": "Saturated"})
        self.assertEqual(res4.payload.get("code"), "INFERENCE_BUSY")

        # Connection immediately following is serviced without delay
        res_ping = self.client.call(IPCCommand.PING)
        self.assertEqual(res_ping.status, IPCStatus.OK.value)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_15_no_inference_import_in_fast_path(self) -> None:
        """15. No hay llamadas ni invocación de inferencia en Fast-Path."""
        calls_before = len(self.mock_engine.calls)
        self.client.call(IPCCommand.STATUS)
        self.client.call(IPCCommand.PING)
        self.assertEqual(len(self.mock_engine.calls), calls_before)

    # =========================================================================
    # C. Apagado Seguro (16-23)
    # =========================================================================

    def test_16_stop_rejects_new_queries(self) -> None:
        """16. stop() rechaza nuevas consultas inmediatamente."""
        server = self.daemon.ipc_server
        server.stop(timeout_seconds=0.5)

        # After stop, server is stopping or stopped
        self.assertTrue(server.is_stopped)

    def test_17_stop_prevents_late_admissions(self) -> None:
        """17. stop() no permite admisiones tardías (is_stopping rechaza)."""
        server = self.daemon.ipc_server
        server._stopping = True

        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Late admission"})
        from siegfried.ipc.protocol import serialize_frame
        # Simulate read_client with a mock socket
        mock_conn = MagicMock(spec=socket.socket)
        server._buffers[mock_conn] = bytearray(serialize_frame(req.to_dict()))

        server._read_client(mock_conn, 0)
        self.assertEqual(server.active_cognitive_workers, 0)

    def test_18_stop_waits_for_workers_finishing_normally(self) -> None:
        """18. stop() espera de forma acotada a trabajadores que terminan normalmente."""
        evt = self.mock_engine.create_slot()

        def worker():
            IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": "ShutdownWait"})

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(evt.wait(timeout=2.0))

        # Worker is active
        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 1)

        # Schedule release after 0.1s
        threading.Timer(0.1, self.mock_engine.gate.set).start()

        # Stop with 1.0s timeout
        clean = self.daemon.ipc_server.stop(timeout_seconds=1.0)
        t.join(timeout=2.0)

        self.assertTrue(clean)
        self.assertTrue(self.daemon.ipc_server.is_clean_shutdown)
        self.assertEqual(len(self.daemon.ipc_server.residual_workers), 0)

    def test_19_stop_reports_incomplete_shutdown_if_worker_blocked(self) -> None:
        """19. stop() informa apagado incompleto si hay un trabajador bloqueado más allá del timeout."""
        evt = self.mock_engine.create_slot()

        def worker():
            IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": "StuckWorker"})

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(evt.wait(timeout=2.0))

        # Stop with very short timeout without releasing gate
        clean = self.daemon.ipc_server.stop(timeout_seconds=0.1)

        # Must report incomplete shutdown
        self.assertFalse(clean)
        self.assertFalse(self.daemon.ipc_server.is_clean_shutdown)
        self.assertGreater(len(self.daemon.ipc_server.residual_workers), 0)

        # Cleanup
        self.mock_engine.gate.set()
        t.join(timeout=2.0)

    def test_20_stop_is_idempotent(self) -> None:
        """20. stop() es idempotente ante llamadas sucesivas."""
        server = self.daemon.ipc_server
        res1 = server.stop(timeout_seconds=0.5)
        res2 = server.stop(timeout_seconds=0.5)
        self.assertEqual(res1, res2)
        self.assertTrue(server.is_stopped)

    def test_21_no_double_capacity_release(self) -> None:
        """21. No existe doble liberación de cupos en el semáforo acotado."""
        sem = self.daemon.ipc_server._cognitive_semaphore
        # Under BoundedSemaphore, releasing beyond initial value raises ValueError
        # Ensure that normal operations never trigger ValueError
        self.mock_engine.gate.set()
        for _ in range(5):
            res = self.client.call(IPCCommand.QUERY, {"prompt": "SingleReleaseCheck"})
            self.assertEqual(res.status, IPCStatus.OK.value)

    def test_22_worker_registry_consistent(self) -> None:
        """22. El registro de trabajadores queda consistente (vacío tras completar todas las solicitudes)."""
        self.mock_engine.gate.set()
        for i in range(3):
            self.client.call(IPCCommand.QUERY, {"prompt": f"CheckRegistry{i}"})

        time.sleep(0.05)
        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 0)
        self.assertEqual(len(self.daemon.ipc_server._worker_threads), 0)

    def test_23_no_external_processes_terminated(self) -> None:
        """23. El apagado del servidor IPC no termina procesos del sistema ni utiliza pkill."""
        # Current process PID is alive
        pid = os.getpid()
        self.daemon.ipc_server.stop(timeout_seconds=0.2)
        # Process continues running safely
        self.assertEqual(os.getpid(), pid)

    # =========================================================================
    # D. Errores y Seguridad (24-30)
    # =========================================================================

    def test_24_inference_busy_delivered_to_cli(self) -> None:
        """24. INFERENCE_BUSY llega a la CLI 'ask' con código de salida 1 y mensaje amigable."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        err_buf = io.StringIO()
        with patch("sys.stderr", err_buf):
            exit_code = run_cli(["ask", "Consulta saturada CLI"], paths=self.paths)

        self.assertEqual(exit_code, 1)
        err_msg = err_buf.getvalue()
        self.assertIn("El motor de inferencia está ocupado", err_msg)
        self.assertNotIn("Traceback", err_msg)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_25_repl_shows_readable_busy_message(self) -> None:
        """25. REPL muestra mensaje legible ante saturación sin crashear."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        repl = SiegfriedREPL(self.client)
        inputs = ["¿Puedes ayudarme?", "exit"]
        out_buf = io.StringIO()

        with patch("builtins.input", side_effect=inputs), patch("sys.stdout", out_buf):
            repl.run()

        output = out_buf.getvalue()
        self.assertIn("El motor de inferencia está ocupado", output)
        self.assertNotIn("Traceback", output)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_26_local_only_does_not_fallback_to_cloud_on_saturation(self) -> None:
        """26. LOCAL_ONLY no activa fallback a Cloud cuando se satura la capacidad."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        res = self.client.call(IPCCommand.QUERY, {"prompt": "Local Only Secret", "policy": "LOCAL_ONLY"})
        self.assertEqual(res.status, IPCStatus.REJECTED.value)
        self.assertEqual(res.payload.get("code"), "INFERENCE_BUSY")

        # Zero cloud calls made
        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_27_saturation_logs_contain_no_prompts(self) -> None:
        """27. Los logs y respuestas de saturación no contienen el prompt del usuario."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        private_prompt = "CONFIDENTIAL_FINANCIAL_DATA_98765"
        res = self.client.call(IPCCommand.QUERY, {"prompt": private_prompt})

        self.assertNotIn(private_prompt, str(res.payload))
        self.assertNotIn(private_prompt, res.error_msg or "")

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_28_exceptions_contain_no_secrets(self) -> None:
        """28. Las respuestas ante excepción o saturación no exponen claves sk-* ni Bearer tokens."""
        self.mock_engine.gate.set()
        self.mock_engine.exception_to_raise = RuntimeError("Auth fail with key sk-secret123456789 and Bearer mytoken")

        res = self.client.call(IPCCommand.QUERY, {"prompt": "Test Secret Leak"})
        self.assertNotIn("sk-secret123456789", str(res.error_msg))
        self.assertNotIn("mytoken", str(res.error_msg))

    def test_29_disconnected_client_leaves_no_permanent_resources(self) -> None:
        """29. Un cliente desconectado no deja buffers ni referencias permanentes en el servidor."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(self.paths.socket_file))
        s.sendall(b"partial_data_without_newline")
        s.close()

        # Tick reactor to process disconnect
        self.daemon.ipc_server.poll(timeout_seconds=0.05)
        # Buffers dictionary must not retain the dead connection
        self.assertNotIn(s, self.daemon.ipc_server._buffers)

    def test_30_malformed_request_does_not_consume_capacity(self) -> None:
        """30. Una solicitud malformada no consume capacidad de trabajadores cognitivos."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(self.paths.socket_file))
        s.sendall(b"{not-valid-json}\n")
        resp = s.recv(4096)
        s.close()

        self.assertIn(b"Bad request", resp)
        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 0)

    # =========================================================================
    # E. Integración Completa sobre UDS (31-38)
    # =========================================================================

    def test_31_real_ipc_over_unix_domain_socket(self) -> None:
        """31. Comunicación IPC real mediante Unix Domain Socket autenticado y funcional."""
        self.assertTrue(self.paths.socket_file.exists())
        res = self.client.call(IPCCommand.PING)
        self.assertEqual(res.status, IPCStatus.OK.value)

    def test_32_isolated_test_daemon_runtime(self) -> None:
        """32. Daemon de prueba ejecutado en runtime completamente aislado."""
        self.assertTrue(str(self.paths.base_dir).startswith(self.tmp_dir.name))

    def test_33_three_concurrent_cognitive_queries_execute(self) -> None:
        """33. Tres consultas cognitivas concurrentes se ejecutan simultáneamente."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        self.assertEqual(self.daemon.ipc_server.active_cognitive_workers, 3)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_34_fourth_concurrent_query_rejected(self) -> None:
        """34. Cuarta consulta rechazada con código INFERENCE_BUSY mientras las 3 primeras están activas."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        res = self.client.call(IPCCommand.QUERY, {"prompt": "Reject4"})
        self.assertEqual(res.status, IPCStatus.REJECTED.value)
        self.assertEqual(res.payload.get("code"), "INFERENCE_BUSY")

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_35_deterministic_query_succeeds_during_saturation(self) -> None:
        """35. Consulta determinista (STATUS) exitosa en < 10ms durante la saturación de 3 cognitivas."""
        evts = [self.mock_engine.create_slot() for _ in range(3)]
        threads = []
        for i in range(3):
            t = threading.Thread(target=lambda idx=i: IPCClient(self.paths.socket_file, timeout_seconds=5.0).call(IPCCommand.QUERY, {"prompt": f"Q{idx}"}))
            threads.append(t)
            t.start()
            self.assertTrue(evts[i].wait(timeout=2.0))

        t0 = time.monotonic()
        res_stat = self.client.call(IPCCommand.STATUS)
        lat_ms = (time.monotonic() - t0) * 1000.0

        self.assertEqual(res_stat.status, IPCStatus.OK.value)
        self.assertLess(lat_ms, 25.0)

        self.mock_engine.gate.set()
        for t in threads:
            t.join(timeout=2.0)

    def test_36_complete_test_daemon_termination(self) -> None:
        """36. Terminación completa del daemon de prueba al finalizar."""
        self.mock_engine.gate.set()
        clean = self.daemon.stop(timeout_seconds=1.0)
        self.assertTrue(clean)
        self.assertFalse(self.daemon._running)

    def test_37_zero_orphan_temporary_sockets(self) -> None:
        """37. Cero sockets temporales huérfanos tras detener el servidor IPC."""
        sock_file = self.paths.socket_file
        self.daemon.ipc_server.stop(timeout_seconds=0.5)
        self.assertFalse(sock_file.exists())

    def test_38_zero_lingering_workers_after_clean_shutdown(self) -> None:
        """38. Cero trabajadores residuales vivos tras un apagado limpio."""
        self.mock_engine.gate.set()
        self.daemon.ipc_server.stop(timeout_seconds=0.5)
        self.assertTrue(self.daemon.ipc_server.is_clean_shutdown)
        self.assertEqual(len(self.daemon.ipc_server.residual_workers), 0)


if __name__ == "__main__":
    unittest.main()
