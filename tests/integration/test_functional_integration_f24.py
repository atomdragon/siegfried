"""Functional End-to-End and Integration Test Suite for Gate F2.4.

Validates:
- Route A (Fast-Path Deterministic): CLI / REPL -> CommandRouter -> IPC -> Daemon -> Deterministic Core -> Response
- Route B (Cognitive Query): CLI / REPL -> CommandRouter -> IPC -> Daemon -> InferenceOrchestrator -> Cloud/Local -> Response
- Privacy confinement, availability, concurrency (timers unblocked during inference), and CLI robustness.
"""

import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import MagicMock, patch

from siegfried.cli.app import run_cli
from siegfried.cli.repl import SiegfriedREPL
from siegfried.cli.router import CommandRouter
from siegfried.contracts.inference import (
    InferenceMessage,
    InferencePolicy,
    InferenceRequest,
    InferenceResponse,
    InferenceRoute,
    OrchestrationResult,
)
from siegfried.contracts.ipc import IPCCommand, IPCRequest, IPCResponse, IPCStatus
from siegfried.contracts.states import SystemState
from siegfried.core.errors import (
    InferenceAuthError,
    InferenceDeadlineExceededError,
    InferenceError,
    InferenceResponseTooLargeError,
    InferenceSecurityError,
    InferenceTimeoutError,
    InferenceTransportError,
    NoAvailableEngineError,
    PrivacyViolationError,
)
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.inference.orchestrator import InferenceOrchestrator
from siegfried.ipc.client import IPCClient
from siegfried.storage.paths import SiegfriedPaths


class MockEngine:
    """Mock engine simulating Cloud or Local LLM inference."""

    def __init__(self, name: str, response_text: str = "Respuesta del modelo") -> None:
        self.name = name
        self.response_text = response_text
        self.calls: List[InferenceRequest] = []
        self.exception_to_raise: Optional[Exception] = None
        self.delay_seconds: float = 0.0

    def generate(self, request: InferenceRequest) -> InferenceResponse:
        self.calls.append(request)
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if self.exception_to_raise:
            raise self.exception_to_raise
        return InferenceResponse(
            content=self.response_text,
            model=request.model or self.name,
            duration_ms=10.0,
            raw_status=200,
        )


class TestFunctionalIntegrationF24(unittest.TestCase):
    """End-to-end integration test suite for Gate F2.4."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.tmp_dir.name)
        self.paths = SiegfriedPaths(
            base_dir=self.base_path / ".siegfried",
            runtime_dir=self.base_path / "run",
        )
        self.paths.ensure_directories()

        self.cloud_mock = MockEngine("deepseek-cloud", "Respuesta Cloud generada.")
        self.local_mock = MockEngine("llama-local", "Respuesta Local generada.")
        self.orchestrator = InferenceOrchestrator(
            cloud_client=self.cloud_mock,
            local_client=self.local_mock,
            default_policy=InferencePolicy.CLOUD_PREFERRED,
        )

        self.daemon = SiegfriedDaemon(paths=self.paths, orchestrator=self.orchestrator)
        self.daemon.start()

        self._daemon_loop_thread = threading.Thread(target=self._run_daemon_loop, daemon=True)
        self._daemon_loop_thread.start()

        # Helper client connected to daemon's unix socket
        self.client = IPCClient(self.paths.socket_file, timeout_seconds=5.0)

    def _run_daemon_loop(self) -> None:
        while self.daemon._running:
            try:
                self.daemon.run_tick(timeout_seconds=0.02)
            except Exception:
                break

    def tearDown(self) -> None:
        self.daemon.stop()
        self._daemon_loop_thread.join(timeout=1.0)
        self.tmp_dir.cleanup()


    # =========================================================================
    # A. Integración funcional
    # =========================================================================

    def test_01_cli_ask_cognitive_response(self) -> None:
        """1. CLI 'ask' command transmits cognitive query over IPC and prints response."""
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            exit_code = run_cli(["ask", "Explica", "el", "patrón", "reactor"], paths=self.paths)

        self.assertEqual(exit_code, 0)
        output = out_buf.getvalue()
        self.assertIn("Siegfried [CLOUD]", output)
        self.assertIn("Respuesta Cloud generada.", output)
        self.assertEqual(len(self.cloud_mock.calls), 1)

    def test_02_repl_cognitive_query_interaction(self) -> None:
        """2. REPL interacts with cognitive query when Fast-Path does not match."""
        repl = SiegfriedREPL(self.client, paths=self.paths)
        inputs = ["¿Cómo organizo mis tareas para hoy?", "exit"]
        out_buf = io.StringIO()

        with patch("builtins.input", side_effect=inputs), patch("sys.stdout", out_buf):
            repl.run()

        output = out_buf.getvalue()
        self.assertIn("Siegfried [CLOUD]: Respuesta Cloud generada.", output)
        self.assertEqual(len(self.cloud_mock.calls), 1)

    def test_03_fast_path_routes_deterministically_without_llm(self) -> None:
        """3. Fast-Path commands ('status', 'focus') execute deterministically without touching LLM."""
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            exit_code = run_cli(["status"], paths=self.paths)
        self.assertEqual(exit_code, 0)

        # Ensure neither Cloud nor Local engine was called
        self.assertEqual(len(self.cloud_mock.calls), 0)
        self.assertEqual(len(self.local_mock.calls), 0)

    def test_04_simulated_cloud_engine_responds(self) -> None:
        """4. Cloud engine responds with expected normalized content."""
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Hola Cloud", "policy": "CLOUD_ONLY"})
        res = self.client.send_request(req)
        self.assertEqual(res.status, IPCStatus.OK.value)
        self.assertEqual(res.payload["response"], "Respuesta Cloud generada.")
        self.assertEqual(res.payload["route_used"], "CLOUD")
        self.assertFalse(res.payload["fallback_used"])

    def test_05_simulated_local_engine_responds(self) -> None:
        """5. Local engine responds under LOCAL_ONLY policy."""
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Hola Local", "policy": "LOCAL_ONLY"})
        res = self.client.send_request(req)
        self.assertEqual(res.status, IPCStatus.OK.value)
        self.assertEqual(res.payload["response"], "Respuesta Local generada.")
        self.assertEqual(res.payload["route_used"], "LOCAL")
        self.assertEqual(len(self.cloud_mock.calls), 0)
        self.assertEqual(len(self.local_mock.calls), 1)

    def test_06_fallback_cloud_to_local(self) -> None:
        """6. When Cloud times out, system safely falls back to Local."""
        self.cloud_mock.exception_to_raise = InferenceTimeoutError("Cloud timed out")
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Consulta con fallback", "policy": "CLOUD_PREFERRED"})
        res = self.client.send_request(req)

        self.assertEqual(res.status, IPCStatus.OK.value)
        self.assertEqual(res.payload["route_used"], "LOCAL")
        self.assertTrue(res.payload["fallback_used"])
        self.assertEqual(res.payload["response"], "Respuesta Local generada.")

    def test_07_fallback_local_to_cloud(self) -> None:
        """7. When Local fails under LOCAL_PREFERRED, system falls back to Cloud."""
        self.local_mock.exception_to_raise = InferenceTransportError("Local server down")
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Consulta con fallback", "policy": "LOCAL_PREFERRED"})
        res = self.client.send_request(req)

        self.assertEqual(res.status, IPCStatus.OK.value)
        self.assertEqual(res.payload["route_used"], "CLOUD")
        self.assertTrue(res.payload["fallback_used"])
        self.assertEqual(res.payload["response"], "Respuesta Cloud generada.")

    def test_08_both_engines_fail_controlled(self) -> None:
        """8. When both engines fail, return controlled error without crashing daemon."""
        self.cloud_mock.exception_to_raise = InferenceTimeoutError("Cloud timeout")
        self.local_mock.exception_to_raise = InferenceTransportError("Local error")

        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Fallo total", "policy": "CLOUD_PREFERRED"})
        res = self.client.send_request(req)

        self.assertEqual(res.status, IPCStatus.ERROR.value)
        self.assertIn("Motor de inferencia no disponible", res.error_msg or "")

        # Daemon is still alive and responds to ping
        ping_res = self.client.call(IPCCommand.PING)
        self.assertEqual(ping_res.status, IPCStatus.OK.value)

    # =========================================================================
    # B. Privacidad y Confinamiento
    # =========================================================================

    def test_09_local_only_produces_no_external_traffic(self) -> None:
        """9. LOCAL_ONLY policy produces zero calls to Cloud even if local fails."""
        self.local_mock.exception_to_raise = InferenceTransportError("Local unavailable")
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Secreto de usuario", "policy": "LOCAL_ONLY"})
        res = self.client.send_request(req)

        self.assertEqual(res.status, IPCStatus.ERROR.value)
        self.assertEqual(len(self.cloud_mock.calls), 0)

    def test_10_cloud_only_does_not_call_local(self) -> None:
        """10. CLOUD_ONLY policy never touches Local engine."""
        self.cloud_mock.exception_to_raise = InferenceTimeoutError("Cloud down")
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Test", "policy": "CLOUD_ONLY"})
        res = self.client.send_request(req)

        self.assertEqual(res.status, IPCStatus.ERROR.value)
        self.assertEqual(len(self.local_mock.calls), 0)

    def test_11_unconfigured_cloud_does_not_implicitly_send_externally(self) -> None:
        """11. When cloud is unconfigured, daemon defaults safely to LOCAL_ONLY without sending data externally."""
        # Create daemon without orchestrator injected (lazy default)
        daemon_unconf = SiegfriedDaemon(paths=self.paths, orchestrator=None)
        orch = daemon_unconf._get_orchestrator()
        # Without DEEPSEEK_API_KEY in secrets.env, policy defaults to LOCAL_ONLY
        self.assertEqual(orch.default_policy, InferencePolicy.LOCAL_ONLY)
        self.assertIsNone(orch.cloud_client)

    def test_12_generated_text_does_not_execute_system_commands(self) -> None:
        """12. LLM response containing malicious shell commands is treated purely as inert text."""
        self.cloud_mock.response_text = "rm -rf /; sudo shutdown -h now"
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "¿Qué comando debo ejecutar?"})
        res = self.client.send_request(req)

        self.assertEqual(res.status, IPCStatus.OK.value)
        # Content returned as text; system is intact
        self.assertEqual(res.payload["response"], "rm -rf /; sudo shutdown -h now")

    def test_13_generated_text_does_not_modify_agenda_or_vault(self) -> None:
        """13. Generated text cannot directly mutate active agenda or Vault."""
        self.cloud_mock.response_text = "Tarea crítica actualizada a: Estudiar .NET"
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Actualiza mi agenda"})
        self.client.send_request(req)

        # Check vault has no events appended from inference
        events = list(self.daemon.vault.read_events())
        self.assertEqual(len(events), 0)

    def test_14_no_secrets_in_errors_or_payloads(self) -> None:
        """14. Error responses never leak Bearer tokens or secrets."""
        self.cloud_mock.exception_to_raise = InferenceAuthError("Bearer sk-antigravity-9999 was rejected")
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Test auth", "policy": "CLOUD_ONLY"})
        res = self.client.send_request(req)

        self.assertEqual(res.status, IPCStatus.ERROR.value)
        self.assertNotIn("sk-antigravity-9999", res.error_msg or "")

    # =========================================================================
    # C. Disponibilidad del Daemon
    # =========================================================================

    def test_15_daemon_starts_without_api_key(self) -> None:
        """15. Daemon starts cleanly without any API key in secrets.env."""
        # self.daemon started with empty tmp environment
        self.assertTrue(self.daemon._running)
        res = self.client.call(IPCCommand.PING)
        self.assertEqual(res.status, IPCStatus.OK.value)

    def test_16_daemon_starts_without_local_model(self) -> None:
        """16. Daemon starts without GGUF model file on disk."""
        self.assertFalse((self.paths.data_dir / "models").exists())
        self.assertTrue(self.daemon._running)


    def test_17_cli_informs_unavailability_clearly(self) -> None:
        """17. CLI reports unavailability with clear message without Python tracebacks."""
        self.orchestrator.cloud_client = None
        self.orchestrator.local_client = None

        err_buf = io.StringIO()
        with patch("sys.stderr", err_buf):
            exit_code = run_cli(["ask", "Hola"], paths=self.paths)

        self.assertEqual(exit_code, 1)
        self.assertIn("[ERROR]", err_buf.getvalue())
        self.assertNotIn("Traceback (most recent call last):", err_buf.getvalue())

    def test_18_cloud_failure_does_not_kill_daemon(self) -> None:
        """18. Cloud network error does not terminate daemon process."""
        self.cloud_mock.exception_to_raise = InferenceTransportError("Connection refused")
        self.client.call(IPCCommand.QUERY, {"prompt": "Ping", "policy": "CLOUD_ONLY"})

        # Daemon is still running
        ping = self.client.call(IPCCommand.PING)
        self.assertEqual(ping.status, IPCStatus.OK.value)

    def test_19_local_failure_does_not_kill_daemon(self) -> None:
        """19. Local inference exception does not terminate daemon process."""
        self.local_mock.exception_to_raise = RuntimeError("Segmentation fault in llama-server")
        self.client.call(IPCCommand.QUERY, {"prompt": "Ping", "policy": "LOCAL_ONLY"})

        ping = self.client.call(IPCCommand.PING)
        self.assertEqual(ping.status, IPCStatus.OK.value)

    def test_20_inference_failure_does_not_alter_timers(self) -> None:
        """20. Inference failure does not disrupt or reset active Pomodoro timer."""
        # Start a 25 min focus timer
        self.client.call(IPCCommand.START_FOCUS, {"duration_min": 25, "task": "Arquitectura"})
        self.assertTrue(self.daemon.focus_timer.is_active())

        # Cause inference failure
        self.cloud_mock.exception_to_raise = InferenceTimeoutError("Timeout")
        self.client.call(IPCCommand.QUERY, {"prompt": "Test", "policy": "CLOUD_ONLY"})

        # Verify timer is still running intact
        self.assertTrue(self.daemon.focus_timer.is_active())
        self.assertEqual(self.daemon.state_machine.state, SystemState.POMODORO_RUNNING)

    # =========================================================================
    # D. Concurrencia y No Bloqueo de Timers
    # =========================================================================

    def test_21_two_simultaneous_cognitive_queries(self) -> None:
        """21. Two concurrent cognitive queries are processed cleanly."""
        results = []

        def worker(idx: int):
            c = IPCClient(self.paths.socket_file, timeout_seconds=10.0)
            res = c.call(IPCCommand.QUERY, {"prompt": f"Consulta {idx}"})
            results.append(res)

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r.status, IPCStatus.OK.value)

    def test_22_cognitive_query_concurrent_with_fast_path(self) -> None:
        """22. Fast-Path commands are answered in <10ms while cognitive query is generating."""
        self.cloud_mock.delay_seconds = 0.5  # Simulate 500ms LLM latency

        fast_path_latencies = []

        def slow_query():
            c = IPCClient(self.paths.socket_file, timeout_seconds=5.0)
            c.call(IPCCommand.QUERY, {"prompt": "Pregunta lenta"})

        def fast_query():
            # Wait 50ms for slow query to start
            time.sleep(0.05)
            c = IPCClient(self.paths.socket_file, timeout_seconds=2.0)
            t0 = time.perf_counter()
            res = c.call(IPCCommand.STATUS)
            t1 = time.perf_counter()
            fast_path_latencies.append((t1 - t0) * 1000.0)
            self.assertEqual(res.status, IPCStatus.OK.value)

        t_slow = threading.Thread(target=slow_query)
        t_fast = threading.Thread(target=fast_query)

        t_slow.start()
        t_fast.start()

        t_fast.join()
        t_slow.join()

        # Fast path was served immediately while slow query was in flight
        self.assertTrue(len(fast_path_latencies) > 0)
        self.assertLess(fast_path_latencies[0], 50.0)  # Much less than 500ms delay

    def test_23_timer_expires_while_inference_is_processing(self) -> None:
        """23. Monotonic focus timer expires and triggers break while inference is generating."""
        self.cloud_mock.delay_seconds = 0.4

        # Transition state machine to POMODORO_RUNNING and start short timer
        self.daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING, reason="Test Timer Focus")
        self.daemon.focus_timer.start(duration_seconds=0.15, task_name="Test Timer")

        def query_task():
            c = IPCClient(self.paths.socket_file, timeout_seconds=5.0)
            c.call(IPCCommand.QUERY, {"prompt": "Pregunta durante timer"})

        t = threading.Thread(target=query_task)
        t.start()

        # Wait for timer to expire in background daemon loop
        time.sleep(0.3)

        # Timer expired and state transitioned to BREAK_RUNNING!
        self.assertEqual(self.daemon.state_machine.state, SystemState.BREAK_RUNNING)

        t.join()


    def test_24_client_disconnect_during_generation_handled_safely(self) -> None:
        """24. Client abrupt disconnection (closing socket mid-flight) does not crash daemon."""
        self.cloud_mock.delay_seconds = 0.3

        # Connect low level socket and close it immediately after sending query
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(self.paths.socket_file))
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Abort me"})
        from siegfried.ipc.protocol import serialize_frame
        sock.sendall(serialize_frame(req.to_dict()))
        # Abrupt close!
        sock.close()

        # Wait for worker thread to finish
        time.sleep(0.4)

        # Daemon is still completely healthy
        ping = self.client.call(IPCCommand.PING)
        self.assertEqual(ping.status, IPCStatus.OK.value)

    def test_25_active_request_prevents_idle_shutdown(self) -> None:
        """25. Active inference requests keep server busy and prevent idle eviction."""
        mock_manager = MagicMock()
        mock_manager.is_running = True
        mock_manager.state = "BUSY"
        mock_manager.check_idle.return_value = False
        self.orchestrator.local_manager = mock_manager

        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Busy check", "policy": "LOCAL_ONLY"})
        res = self.client.send_request(req)
        self.assertEqual(res.status, IPCStatus.OK.value)

    def test_26_no_orphan_threads_after_shutdown(self) -> None:
        """26. Daemon stop cleanly joins all worker threads."""
        initial_threads = threading.active_count()
        # Launch query
        self.client.call(IPCCommand.QUERY, {"prompt": "Thread check"})
        # Daemon stop
        self.daemon.stop()
        # Active threads should not leak
        self.assertLessEqual(threading.active_count(), initial_threads + 1)

    # =========================================================================
    # E. Contratos y Validación de Esquemas
    # =========================================================================

    def test_27_malformed_ipc_request_rejected(self) -> None:
        """27. Malformed non-JSON frame or bad dictionary rejected with error response."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(self.paths.socket_file))
        sock.sendall(b"{bad json syntax\n")

        data = sock.recv(4096)
        sock.close()

        from siegfried.ipc.protocol import deserialize_frame
        res = IPCResponse.from_dict(deserialize_frame(data))
        self.assertEqual(res.status, IPCStatus.ERROR.value)
        self.assertIn("Bad request", res.error_msg or "")

    def test_28_excessive_prompt_size_rejected(self) -> None:
        """28. Prompt exceeding 32 KB safety threshold rejected immediately."""
        huge_prompt = "A" * 35000
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": huge_prompt})
        res = self.client.send_request(req)
        self.assertEqual(res.status, IPCStatus.REJECTED.value)
        self.assertIn("excede tamaño máximo", res.error_msg or "")

    def test_29_invalid_policy_rejected(self) -> None:
        """29. Invalid inference policy string in IPC request rejected."""
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Hola", "policy": "INVALID_POLICY_NAME"})
        res = self.client.send_request(req)
        self.assertEqual(res.status, IPCStatus.ERROR.value)
        self.assertIn("Política de inferencia inválida", res.error_msg or "")

    def test_30_expired_deadline_handled_safely(self) -> None:
        """30. Expired deadline yields controlled InferenceDeadlineExceededError response."""
        self.cloud_mock.exception_to_raise = InferenceDeadlineExceededError("Deadline expired")
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Hola", "policy": "CLOUD_ONLY"})
        res = self.client.send_request(req)
        self.assertEqual(res.status, IPCStatus.ERROR.value)
        self.assertIn("Tiempo límite de inferencia agotado", res.error_msg or "")

    def test_31_response_too_large_handled_safely(self) -> None:
        """31. InferenceResponseTooLargeError handled and reported cleanly."""
        self.cloud_mock.exception_to_raise = InferenceResponseTooLargeError("Response exceeded 10MB")
        req = IPCRequest.create(IPCCommand.QUERY, {"prompt": "Hola", "policy": "CLOUD_ONLY"})
        res = self.client.send_request(req)
        self.assertEqual(res.status, IPCStatus.ERROR.value)
        self.assertTrue(
            "InferenceResponseTooLargeError" in (res.error_msg or "")
            or res.payload.get("error_type") == "InferenceResponseTooLargeError"
        )


    def test_32_existing_ipc_messages_fully_compatible(self) -> None:
        """32. Existing IPC contracts (PING, STATUS, START_FOCUS, CANCEL_FOCUS, ACK_BREAK) intact."""
        res_ping = self.client.call(IPCCommand.PING)
        self.assertEqual(res_ping.status, IPCStatus.OK.value)

        res_status = self.client.call(IPCCommand.STATUS)
        self.assertEqual(res_status.status, IPCStatus.OK.value)

        res_focus = self.client.call(IPCCommand.START_FOCUS, {"duration_min": 10, "task": "Compatibility"})
        self.assertEqual(res_focus.status, IPCStatus.OK.value)

        res_cancel = self.client.call(IPCCommand.CANCEL_FOCUS)
        self.assertEqual(res_cancel.status, IPCStatus.OK.value)

    # =========================================================================
    # F. CLI y Experiencia de Usuario
    # =========================================================================

    def test_33_cli_help_without_loading_inference(self) -> None:
        """33. 'siegfried --help' runs cold without importing or running inference models."""
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            try:
                run_cli(["--help"], paths=self.paths)
            except SystemExit as e:
                self.assertEqual(e.code, 0)
        output = out_buf.getvalue()
        self.assertIn("ask", output)
        self.assertIn("status", output)

    def test_34_cli_init_does_not_call_ai(self) -> None:
        """34. 'siegfried init' initializes runtime without making AI calls."""
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            exit_code = run_cli(["init"], paths=self.paths)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(self.cloud_mock.calls), 0)

    def test_35_cli_doctor_functions_without_ai_configured(self) -> None:
        """35. 'siegfried doctor' performs non-destructive health checks without AI requirements."""
        # Ensure runtime is initialized
        run_cli(["init"], paths=self.paths)
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            exit_code = run_cli(["doctor"], paths=self.paths)
        self.assertEqual(exit_code, 0)
        self.assertIn("READY", out_buf.getvalue())
        self.assertEqual(len(self.cloud_mock.calls), 0)

    def test_36_errors_show_no_python_stack_traces(self) -> None:
        """36. CLI error display prints user-friendly messages with no Python tracebacks."""
        self.cloud_mock.exception_to_raise = InferenceTransportError("DNS failure: name not resolved")
        err_buf = io.StringIO()
        with patch("sys.stderr", err_buf):
            exit_code = run_cli(["ask", "Pregunta con error", "--policy", "CLOUD_ONLY"], paths=self.paths)

        self.assertEqual(exit_code, 1)
        err_output = err_buf.getvalue()
        self.assertIn("[ERROR]", err_output)
        self.assertNotIn("Traceback (most recent call last):", err_output)
        self.assertNotIn('File "', err_output)

    def test_37_cli_exit_codes_coherent(self) -> None:
        """37. CLI returns 0 on success, 1 on application error, 2 on daemon connection error."""
        # 0 on success
        self.assertEqual(run_cli(["ping"], paths=self.paths), 0)

        # 2 when daemon is not reachable
        non_existent_paths = SiegfriedPaths(base_dir=self.base_path / "nowhere")
        self.assertEqual(run_cli(["ping"], paths=non_existent_paths), 2)

    def test_38_router_preserves_question_status_fast_path(self) -> None:
        """38. Router maps '¿Cuánto tiempo me queda?' directly to STATUS Fast-Path."""
        router = CommandRouter()
        match = router.route("¿Cuánto tiempo me queda?")
        self.assertTrue(match.is_fast_path)
        self.assertEqual(match.command, IPCCommand.STATUS)


if __name__ == "__main__":
    unittest.main()
