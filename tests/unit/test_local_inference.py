"""Comprehensive unit tests for local inference, llama_manager supervisor, and resource budgets."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from siegfried.contracts.inference import (
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    InferenceUsage,
)
from siegfried.core.errors import (
    InferenceConcurrencyExceededError,
    InferenceConfigError,
    InferenceDeadlineExceededError,
    InferenceHTTPError,
    InferenceRateLimitError,
    InferenceResponseError,
    InferenceResponseTooLargeError,
    InferenceSecurityError,
    InferenceTimeoutError,
    InsufficientResourcesError,
    LlamaBinaryNotFoundError,
    LlamaHealthCheckError,
    LlamaModelNotFoundError,
    LlamaProcessTerminatedError,
    LlamaStartupError,
    LocalInferenceUnavailableError,
    PortInUseError,
)
from siegfried.inference.cloud import InferenceEngine
from siegfried.inference.llama_manager import LlamaLifecycleManager, LlamaServerState
from siegfried.inference.local import LocalInferenceClient, LocalInferenceStub
from siegfried.inference.resources import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_MAX_GPU_BUDGET_BYTES,
    DEFAULT_MIN_OS_RAM_RESERVATION_BYTES,
    ResourceBudget,
    ResourceDetector,
    SystemResources,
)
from siegfried.storage.paths import SiegfriedPaths


class MockLocalServerHandler(BaseHTTPRequestHandler):
    """Local mock server responding to /health and /v1/chat/completions."""

    health_status_code = 200
    health_payload = {"status": "ok"}
    chat_status_code = 200
    chat_payload = {}
    delay_seconds = 0.0

    def do_GET(self):
        if self.path == "/health":
            self.send_response(MockLocalServerHandler.health_status_code)
            self.send_header("Content-Type", "application/json")
            resp_bytes = json.dumps(MockLocalServerHandler.health_payload).encode("utf-8")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if MockLocalServerHandler.delay_seconds > 0:
            time.sleep(MockLocalServerHandler.delay_seconds)

        if self.path in ("/v1/chat/completions", "/completion"):
            self.send_response(MockLocalServerHandler.chat_status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if isinstance(MockLocalServerHandler.chat_payload, (dict, list)):
                resp_bytes = json.dumps(MockLocalServerHandler.chat_payload).encode("utf-8")
            else:
                resp_bytes = str(MockLocalServerHandler.chat_payload).encode("utf-8")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class TestLocalInferenceSupervisorAndClient(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockLocalServerHandler)
        cls.port = cls.server.server_port
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MockLocalServerHandler.health_status_code = 200
        MockLocalServerHandler.health_payload = {"status": "ok"}
        MockLocalServerHandler.chat_status_code = 200
        MockLocalServerHandler.delay_seconds = 0.0
        MockLocalServerHandler.chat_payload = {
            "id": "chatcmpl-local-01",
            "model": "qwen2.5-3b-local",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "A la orden, Señor. Ejecutando análisis en hardware local.",
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 15,
                "total_tokens": 25
            }
        }

        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.paths = SiegfriedPaths(base_dir=self.base_dir, runtime_dir=self.base_dir / "run")
        self.paths.ensure_directories()

        # Create dummy binary and model
        self.dummy_binary = self.base_dir / "bin" / "llama-server"
        self.dummy_binary.parent.mkdir(parents=True, exist_ok=True)
        self.dummy_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(self.dummy_binary, 0o755)

        self.dummy_model = self.base_dir / "models" / "test-model.gguf"
        self.dummy_model.parent.mkdir(parents=True, exist_ok=True)
        self.dummy_model.write_bytes(b"GGUF" + b"\x00" * 1024 * 1024)  # 1 MiB dummy model

        # Mock clock
        self.simulated_time = 1000.0

        def fake_clock() -> float:
            return self.simulated_time

        self.fake_clock = fake_clock

        # Create manager instance
        self.manager = LlamaLifecycleManager(
            binary_path=self.dummy_binary,
            model_path=self.dummy_model,
            host="127.0.0.1",
            port=self.port,
            clock=self.fake_clock,
            health_check_timeout=0.2,
            max_startup_seconds=2.0,
            paths=self.paths,
            check_port_in_use=False,
        )

        self.client = LocalInferenceClient(
            manager=self.manager,
            endpoint_url=f"http://127.0.0.1:{self.port}",
            timeout_seconds=2.0,
        )

    def tearDown(self):
        if self.manager:
            self.manager.stop_server(timeout=1.0)
        self.temp_dir.cleanup()

    # ─── RECURSOS ─────────────────────────────────────────────

    # 1. CPU-only sin GPU
    def test_01_cpu_only_without_gpu(self):
        budget = ResourceBudget(force_cpu_only=True)
        resources = SystemResources(
            total_ram_bytes=16 * 1024 * 1024 * 1024,
            available_ram_bytes=8 * 1024 * 1024 * 1024,
            gpu_detected=True,
            dedicated_vram_bytes=8 * 1024 * 1024 * 1024,
        )
        layers = budget.calculate_gpu_layers(
            total_layers=32,
            estimated_bytes_per_layer=100 * 1024 * 1024,
            resources=resources,
        )
        self.assertEqual(layers, 0)

    # 2. RAM insuficiente
    def test_02_insufficient_ram_raises_error(self):
        budget = ResourceBudget(min_os_ram_reservation_bytes=2 * 1024 * 1024 * 1024)
        resources = SystemResources(
            total_ram_bytes=4 * 1024 * 1024 * 1024,
            available_ram_bytes=2 * 1024 * 1024 * 1024,  # Only 2 GB available
        )
        model_size = 3 * 1024 * 1024 * 1024  # 3 GB model
        with self.assertRaises(InsufficientResourcesError):
            budget.validate_memory_headroom(model_size, resources)

    # 3. Recursos suficientes
    def test_03_sufficient_resources_pass(self):
        budget = ResourceBudget(min_os_ram_reservation_bytes=2 * 1024 * 1024 * 1024)
        resources = SystemResources(
            total_ram_bytes=16 * 1024 * 1024 * 1024,
            available_ram_bytes=8 * 1024 * 1024 * 1024,
        )
        model_size = 2 * 1024 * 1024 * 1024  # 2 GB model
        # Should not raise
        budget.validate_memory_headroom(model_size, resources)

    # 4. VRAM desconocida
    def test_04_vram_unknown_caps_at_3gib_ceiling(self):
        budget = ResourceBudget(max_gpu_budget_bytes=3 * 1024 * 1024 * 1024)
        resources = SystemResources(
            total_ram_bytes=32 * 1024 * 1024 * 1024,
            available_ram_bytes=16 * 1024 * 1024 * 1024,
            gpu_detected=True,
            dedicated_vram_bytes=None,  # UNKNOWN
            is_integrated_gpu=False,
        )
        # 32 layers of 200 MiB each (total 6.4 GiB)
        layers = budget.calculate_gpu_layers(
            total_layers=32,
            estimated_bytes_per_layer=200 * 1024 * 1024,
            resources=resources,
        )
        # Ceiling: 3.0 GiB / 200 MiB = 15 layers
        self.assertEqual(layers, 15)

    # 5. Límite GPU de 3 GiB
    def test_05_gpu_ceiling_strictly_respected_with_large_vram(self):
        budget = ResourceBudget(max_gpu_budget_bytes=3 * 1024 * 1024 * 1024)
        resources = SystemResources(
            total_ram_bytes=64 * 1024 * 1024 * 1024,
            available_ram_bytes=32 * 1024 * 1024 * 1024,
            gpu_detected=True,
            dedicated_vram_bytes=16 * 1024 * 1024 * 1024,  # 16 GB VRAM physical
            is_integrated_gpu=False,
        )
        layers = budget.calculate_gpu_layers(
            total_layers=40,
            estimated_bytes_per_layer=100 * 1024 * 1024,  # 100 MiB per layer
            resources=resources,
        )
        # 3 GiB = 30 layers max
        self.assertEqual(layers, 30)

    # 6. Reserva de memoria del sistema
    def test_06_os_memory_reservation_enforced(self):
        budget = ResourceBudget(min_os_ram_reservation_bytes=4 * 1024 * 1024 * 1024)
        resources = SystemResources(
            total_ram_bytes=8 * 1024 * 1024 * 1024,
            available_ram_bytes=4 * 1024 * 1024 * 1024 + 100,
        )
        with self.assertRaises(InsufficientResourcesError):
            budget.validate_memory_headroom(1024 * 1024 * 1024, resources)

    # ─── ARRANQUE ─────────────────────────────────────────────

    # 7. Ejecutable inexistente
    def test_07_missing_binary_raises_error(self):
        mgr = LlamaLifecycleManager(
            binary_path=self.base_dir / "nonexistent" / "llama-server",
            model_path=self.dummy_model,
        )
        with self.assertRaises(LlamaBinaryNotFoundError):
            mgr.ensure_started()
        self.assertEqual(mgr.state, LlamaServerState.UNAVAILABLE)

    # 8. Modelo inexistente
    def test_08_missing_model_raises_error(self):
        mgr = LlamaLifecycleManager(
            binary_path=self.dummy_binary,
            model_path=self.base_dir / "nonexistent" / "model.gguf",
        )
        with self.assertRaises(LlamaModelNotFoundError):
            mgr.ensure_started()
        self.assertEqual(mgr.state, LlamaServerState.UNAVAILABLE)

    # 9. Modelo no legible
    def test_09_unreadable_model_raises_error(self):
        os.chmod(self.dummy_model, 0o000)
        try:
            with self.assertRaises(LlamaModelNotFoundError):
                self.manager.ensure_started()
            self.assertEqual(self.manager.state, LlamaServerState.UNAVAILABLE)
        finally:
            os.chmod(self.dummy_model, 0o644)

    # 10. Arranque exitoso simulado
    def test_10_successful_simulated_startup(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43210
        mock_proc.poll.return_value = None

        with patch("subprocess.Popen", return_value=mock_proc):
            started = self.manager.ensure_started()
            self.assertTrue(started)
            self.assertEqual(self.manager.state, LlamaServerState.READY)
            self.assertTrue(self.manager.is_running)
            self.assertTrue(self.paths.llama_pid_file.exists())
            self.assertIn("43210", self.paths.llama_pid_file.read_text())

    # 11. Proceso termina antes de READY
    def test_11_process_terminates_before_ready(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43211
        mock_proc.poll.return_value = 127  # Exited with error

        with patch("subprocess.Popen", return_value=mock_proc):
            with self.assertRaises(LlamaProcessTerminatedError) as ctx:
                self.manager.ensure_started()
            self.assertEqual(ctx.exception.exit_code, 127)
            self.assertEqual(self.manager.state, LlamaServerState.FAILED)

    # 12. Health check no alcanza READY
    def test_12_health_check_times_out(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43212
        mock_proc.poll.return_value = None

        MockLocalServerHandler.health_status_code = 503
        MockLocalServerHandler.health_payload = {"status": "loading model"}

        mgr = LlamaLifecycleManager(
            binary_path=self.dummy_binary,
            model_path=self.dummy_model,
            host="127.0.0.1",
            port=self.port,
            health_check_timeout=0.05,
            max_startup_seconds=0.1,
            paths=self.paths,
            check_port_in_use=False,
        )

        with patch("subprocess.Popen", return_value=mock_proc):
            with self.assertRaises(LlamaHealthCheckError):
                mgr.ensure_started()
            self.assertEqual(mgr.state, LlamaServerState.STOPPED)


    # 13. Puerto ocupado
    def test_13_port_in_use_rejected(self):
        # We start a socket on another port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy_port = sock.getsockname()[1]

        mgr = LlamaLifecycleManager(
            binary_path=self.dummy_binary,
            model_path=self.dummy_model,
            port=busy_port,
            check_port_in_use=True,
        )
        try:
            with self.assertRaises(PortInUseError):
                mgr.ensure_started()
        finally:
            sock.close()

    # 14. Segundo arranque concurrente es idempotente
    def test_14_repeated_ensure_started_idempotent(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43214
        mock_proc.poll.return_value = None

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            res1 = self.manager.ensure_started()
            res2 = self.manager.ensure_started()
            self.assertTrue(res1)
            self.assertTrue(res2)
            # Popen should only have been called once
            self.assertEqual(mock_popen.call_count, 1)

    # ─── OPERACIÓN ────────────────────────────────────────────

    # 15. Inferencia local válida
    def test_15_valid_local_inference(self):
        # Set state to READY directly for mock client test
        self.manager._state = LlamaServerState.READY
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        self.manager._process = mock_proc

        req = InferenceRequest(
            messages=[InferenceMessage(role="user", content="Estado local")],
            model="qwen2.5-3b-local",
        )
        res = self.client.generate(req)
        self.assertIsInstance(res, InferenceResponse)
        self.assertEqual(res.content, "A la orden, Señor. Ejecutando análisis en hardware local.")
        self.assertEqual(res.model, "qwen2.5-3b-local")
        self.assertEqual(res.usage.total_tokens, 25)

    # 16. Error HTTP local
    def test_16_local_http_error(self):
        self.manager._state = LlamaServerState.READY
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        self.manager._process = mock_proc

        MockLocalServerHandler.chat_status_code = 500
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        with self.assertRaises(InferenceHTTPError) as ctx:
            self.client.generate(req)
        self.assertEqual(ctx.exception.status_code, 500)

    # 17. Respuesta JSON inválida
    def test_17_invalid_json_response(self):
        self.manager._state = LlamaServerState.READY
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        self.manager._process = mock_proc

        MockLocalServerHandler.chat_payload = "<html>Invalid JSON</html>"
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        with self.assertRaises(InferenceResponseError):
            self.client.generate(req)

    # 18. Deadline expirado
    def test_18_deadline_expired_before_call(self):
        self.manager._state = LlamaServerState.READY
        past_deadline = time.monotonic() - 1.0
        req = InferenceRequest(
            messages=[InferenceMessage(role="user", content="Hola")],
            deadline=past_deadline,
        )
        with self.assertRaises(InferenceDeadlineExceededError):
            self.client.generate(req)

    # 19. Respuesta demasiado grande
    def test_19_response_too_large(self):
        self.manager._state = LlamaServerState.READY
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        self.manager._process = mock_proc

        # Use 100 bytes max limit on client
        client = LocalInferenceClient(
            manager=self.manager,
            endpoint_url=f"http://127.0.0.1:{self.port}",
            max_response_bytes=20,  # 20 bytes max
        )
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        with self.assertRaises(InferenceResponseTooLargeError):
            client.generate(req)

    # 20. Solicitudes concurrentes por encima del límite
    def test_20_concurrency_limit_exceeded(self):
        self.manager._state = LlamaServerState.READY
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        self.manager._process = mock_proc

        with self.manager.active_inference():
            self.assertEqual(self.manager.state, LlamaServerState.BUSY)
            self.assertEqual(self.manager.active_requests, 1)
            # Second concurrent request must raise InferenceConcurrencyExceededError
            with self.assertRaises(InferenceConcurrencyExceededError):
                with self.manager.active_inference():
                    pass

        self.assertEqual(self.manager.state, LlamaServerState.READY)
        self.assertEqual(self.manager.active_requests, 0)

    # ─── APAGADO ──────────────────────────────────────────────

    # 21. SIGTERM exitoso
    def test_21_sigterm_graceful_shutdown(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43221
        mock_proc.poll.return_value = None

        self.manager._process = mock_proc
        self.manager._state = LlamaServerState.READY
        self.manager._write_pid_file(mock_proc.pid)

        stopped = self.manager.stop_server(timeout=1.0)
        self.assertTrue(stopped)
        mock_proc.terminate.assert_called_once()
        self.assertEqual(self.manager.state, LlamaServerState.STOPPED)
        self.assertFalse(self.paths.llama_pid_file.exists())

    # 22. Escalamiento SIGKILL contra proceso propio
    def test_22_sigkill_escalation_on_timeout(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43222
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="llama-server", timeout=1.0), None]

        self.manager._process = mock_proc
        self.manager._state = LlamaServerState.READY

        self.manager.stop_server(timeout=1.0)
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        self.assertEqual(self.manager.state, LlamaServerState.STOPPED)

    # 23. Apagado idempotente
    def test_23_shutdown_is_idempotent(self):
        self.manager._state = LlamaServerState.STOPPED
        self.manager._process = None
        self.assertTrue(self.manager.stop_server())
        self.assertTrue(self.manager.stop_server())

    # 24. Proceso ajeno nunca terminado
    def test_24_foreign_process_never_terminated(self):
        # If manager does not own the process, stop_server does not terminate external PIDs
        self.manager._process = None
        with patch("os.kill") as mock_kill:
            self.manager.stop_server()
            mock_kill.assert_not_called()

    # 25. Timeout por inactividad
    def test_25_idle_timeout_stops_server(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43225
        mock_proc.poll.return_value = None

        self.manager._process = mock_proc
        self.manager._state = LlamaServerState.READY
        self.manager._last_activity_time = 1000.0

        # Advance simulated time past 15 min (900 s) -> 1901.0
        stopped = self.manager.check_idle(now=1901.0)
        self.assertTrue(stopped)
        self.assertEqual(self.manager.state, LlamaServerState.STOPPED)
        mock_proc.terminate.assert_called_once()

    # 26. Solicitud activa impide apagado por inactividad
    def test_26_active_request_prevents_idle_shutdown(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43226
        mock_proc.poll.return_value = None

        self.manager._process = mock_proc
        self.manager._state = LlamaServerState.READY
        self.manager._last_activity_time = 1000.0

        with self.manager.active_inference():
            # Simulated time past 15 min while busy
            stopped = self.manager.check_idle(now=2500.0)
            self.assertFalse(stopped)
            self.assertEqual(self.manager.state, LlamaServerState.BUSY)
            mock_proc.terminate.assert_not_called()

    # 27. Recuperación tras salida inesperada
    def test_27_crash_detection_and_recovery(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43227
        mock_proc.poll.return_value = -9  # Killed by SIGKILL

        self.manager._process = mock_proc
        self.manager._state = LlamaServerState.READY

        # Accessing state should detect death
        self.assertEqual(self.manager.state, LlamaServerState.FAILED)
        self.assertFalse(self.manager.is_running)

    # 28. Ausencia de zombies tras las pruebas
    def test_28_no_zombies_created(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43228
        mock_proc.poll.return_value = None

        self.manager._process = mock_proc
        self.manager._state = LlamaServerState.READY
        self.manager.stop_server()
        mock_proc.wait.assert_called()

    # ─── SEGURIDAD ────────────────────────────────────────────

    # 29. Rechazo de bind público
    def test_29_public_bind_rejected(self):
        for bad_host in ["0.0.0.0", "192.168.1.50", "8.8.8.8"]:
            mgr = LlamaLifecycleManager(
                binary_path=self.dummy_binary,
                model_path=self.dummy_model,
                host=bad_host,
            )
            with self.assertRaises(InferenceSecurityError):
                mgr.validate_prerequisites()

    # 30. Rechazo de rutas no autorizadas o symlinks rotos
    def test_30_broken_symlink_model_rejected(self):
        broken_symlink = self.base_dir / "models" / "broken_model.gguf"
        os.symlink(self.base_dir / "models" / "non_existent_target.gguf", broken_symlink)
        mgr = LlamaLifecycleManager(
            binary_path=self.dummy_binary,
            model_path=broken_symlink,
        )
        with self.assertRaises(LlamaModelNotFoundError):
            mgr.validate_prerequisites()

    # 31. Sin ejecución shell
    def test_31_no_shell_execution(self):
        mock_proc = MagicMock()
        mock_proc.pid = 43231
        mock_proc.poll.return_value = None

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            self.manager.ensure_started()
            # Verify shell=False was passed
            _, kwargs = mock_popen.call_args
            self.assertFalse(kwargs.get("shell", True))

    # 32. Sin modificación de Vault o agenda
    def test_32_vault_and_agenda_untouched(self):
        vault_file = self.paths.vault_file
        vault_file.write_text('{"v":1,"ts":1.0,"type":"pomodoro_started","data":{}}\n', encoding="utf-8")
        agenda_file = self.paths.active_agenda_file
        agenda_file.write_text('{"v":1,"critical_task":null,"secondary_tasks":[],"backlog":[]}\n', encoding="utf-8")

        v_before = vault_file.read_text(encoding="utf-8")
        a_before = agenda_file.read_text(encoding="utf-8")

        self.manager._state = LlamaServerState.READY
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        self.manager._process = mock_proc

        self.client.generate(InferenceRequest(messages=[InferenceMessage(role="user", content="test")]))

        self.assertEqual(vault_file.read_text(encoding="utf-8"), v_before)
        self.assertEqual(agenda_file.read_text(encoding="utf-8"), a_before)

    # 33. Sin logs de secretos
    def test_33_status_info_no_secrets(self):
        info = self.manager.get_status_info()
        info_str = json.dumps(info)
        for pattern in ["sk-", "Bearer", "api_key", "password"]:
            self.assertNotIn(pattern, info_str)

    # 34. Stub local y compatibilidad con InferenceEngine protocol
    def test_34_local_stub_and_protocol(self):
        stub = LocalInferenceStub()
        self.assertTrue(isinstance(stub, InferenceEngine))
        res = stub.generate(InferenceRequest(messages=[InferenceMessage(role="user", content="Hi")]))
        self.assertIn("LocalInferenceStub", res.content)

        self.assertTrue(isinstance(self.client, InferenceEngine))
        self.manager._state = LlamaServerState.READY
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        self.manager._process = mock_proc
        resp_text = self.client.generate_response([{"role": "user", "content": "Hi"}], mode="EJECUTIVO")
        self.assertIn("A la orden", resp_text)


if __name__ == "__main__":
    unittest.main()
