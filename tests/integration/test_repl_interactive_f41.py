"""Integration and Functional Test Suite for Gate F4.1.

Validates:
- Group A: REPL interaction & session lifecycle (banner, prompt, unicode, EOF, signals, history file).
- Group B: Fast-Path deterministic routing (status, ping, bloque, ack, cancelar, posponer, ayuda, zero LLM).
- Group C: Cognitive inference & backpressure (cloud/local mock, fallback, busy notice, Ctrl+C query cancel).
- Group D: Privacy, context & historical aggregator (ephemeral context, vault isolation, reverse line reader, metrics).
- Group E: Integration & E2E (real REPL + real daemon over Unix socket, emergency quick silence, clean exit).
"""

import io
import json
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
from siegfried.contracts.config import get_default_core_profile
from siegfried.contracts.events import Event, EventType
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
    InferenceError,
    InferenceTimeoutError,
    IPCCommunicationError,
)
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.inference.orchestrator import InferenceOrchestrator
from siegfried.ipc.client import IPCClient
from siegfried.storage.aggregator import AggregatedMetrics, HistoricalAggregator
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault


class MockIPCClient:
    """Mock IPC client for testing REPL commands and responses."""

    def __init__(self, ping_pong: bool = True, ping_state: str = "IDLE") -> None:
        self.ping_pong = ping_pong
        self.ping_state = ping_state
        self.sent_requests: List[IPCRequest] = []
        self.responses: Dict[str, IPCResponse] = {}
        self.default_response = IPCResponse.ok("default", {})
        self.raise_on_command: Dict[str, Exception] = {}
        self.audio_playing: bool = False

    def send_request(self, request: IPCRequest, timeout_seconds: Optional[float] = None) -> IPCResponse:
        self.sent_requests.append(request)
        cmd_str = request.cmd.value if hasattr(request.cmd, "value") else str(request.cmd)
        if cmd_str in self.raise_on_command:
            raise self.raise_on_command[cmd_str]

        if cmd_str == IPCCommand.PING.value:
            if not self.ping_pong:
                raise IPCCommunicationError("Socket not responding")
            return IPCResponse.ok(
                request.request_id,
                {"pong": True, "state": self.ping_state, "audio_playing": self.audio_playing},
            )

        return self.responses.get(cmd_str, self.default_response)

    def call(
        self,
        cmd: IPCCommand | str,
        args: Dict[str, Any] | None = None,
        timeout_seconds: Optional[float] = None,
    ) -> IPCResponse:
        req = IPCRequest.create(cmd, args)
        return self.send_request(req, timeout_seconds=timeout_seconds)


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


# ==============================================================================
# Group A: REPL interaction & session lifecycle
# ==============================================================================

class TestReplSessionLifecycle(unittest.TestCase):
    """Test REPL interactive shell session management and signals."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.paths = SiegfriedPaths(base_dir=Path(self.temp_dir.name))
        self.mock_client = MockIPCClient()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_repl_banner_and_prompt(self) -> None:
        """REPL prints the welcome banner and help hints upon entry."""
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("Siegfried v1.0", out_text)
        self.assertIn("salir", out_text)

    def test_repl_empty_inputs_and_whitespace(self) -> None:
        """Empty lines or whitespace-only inputs cause no crashes and send no IPC queries."""
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["", "   ", "\t  ", "salir"]):
            repl.run()
        query_requests = [r for r in self.mock_client.sent_requests if r.cmd == IPCCommand.QUERY.value]
        self.assertEqual(len(query_requests), 0)

    def test_repl_unicode_handling(self) -> None:
        """Unicode characters (accents, emojis) are routed and handled properly."""
        self.mock_client.responses[IPCCommand.QUERY.value] = IPCResponse.ok(
            "1",
            {"response": "¡Hola! Estoy listo para ayudarte con tu sesión de trabajo. 🧠", "route_used": "LOCAL"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["¿cómo estás hoy? 🚀", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("¡Hola!", out_text)
        self.assertIn("🧠", out_text)
        query_requests = [r for r in self.mock_client.sent_requests if r.cmd == IPCCommand.QUERY.value]
        self.assertEqual(len(query_requests), 1)
        self.assertEqual(query_requests[0].args["prompt"], "¿cómo estás hoy? 🚀")

    def test_repl_multi_turn_history(self) -> None:
        """Multiple conversational turns accumulate in-memory context and pass it to daemon."""
        self.mock_client.responses[IPCCommand.QUERY.value] = IPCResponse.ok(
            "1",
            {"response": "Entendido.", "route_used": "LOCAL"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths, max_context_turns=3)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["Pregunta 1", "Pregunta 2", "salir"]):
            repl.run()

        # Two query requests sent
        query_requests = [r for r in self.mock_client.sent_requests if r.cmd == IPCCommand.QUERY.value]
        self.assertEqual(len(query_requests), 2)

        # First request has 1 message (user)
        self.assertEqual(len(query_requests[0].args["messages"]), 1)
        self.assertEqual(query_requests[0].args["messages"][0]["role"], "user")
        self.assertEqual(query_requests[0].args["messages"][0]["content"], "Pregunta 1")

        # Second request has 3 messages: [user: "Pregunta 1", assistant: "Entendido.", user: "Pregunta 2"]
        self.assertEqual(len(query_requests[1].args["messages"]), 3)
        self.assertEqual(query_requests[1].args["messages"][0]["role"], "user")
        self.assertEqual(query_requests[1].args["messages"][0]["content"], "Pregunta 1")
        self.assertEqual(query_requests[1].args["messages"][1]["role"], "assistant")
        self.assertEqual(query_requests[1].args["messages"][1]["content"], "Entendido.")
        self.assertEqual(query_requests[1].args["messages"][2]["role"], "user")
        self.assertEqual(query_requests[1].args["messages"][2]["content"], "Pregunta 2")

        # In-memory history length should be 4 (2 turns: 2 user + 2 assistant)
        self.assertEqual(len(repl.conversation_history), 4)

    def test_repl_eof_ctrl_d_exit(self) -> None:
        """EOF (Ctrl+D) triggers clean exit with farewell message."""
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=EOFError):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("Hasta luego", out_text)

    def test_repl_keyboard_interrupt_prompt_preserves_session(self) -> None:
        """KeyboardInterrupt at the prompt prints newline and keeps REPL active."""
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=[KeyboardInterrupt, "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("Hasta luego", out_text)

    def test_repl_exit_commands(self) -> None:
        """'salir', 'exit', and 'quit' all terminate REPL gracefully."""
        for cmd in ["salir", "exit", "quit", "SALIR", "EXIT"]:
            repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
            output = io.StringIO()
            with patch("sys.stdout", output), patch("builtins.input", side_effect=[cmd]):
                repl.run()
            self.assertIn("Hasta luego", output.getvalue())

    def test_repl_error_rendering_no_traceback(self) -> None:
        """IPCCommunicationError is rendered cleanly without python stack traces."""
        self.mock_client.raise_on_command[IPCCommand.QUERY.value] = IPCCommunicationError("Socket disconnected")
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["test query", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("No se pudo conectar con el daemon", out_text)
        self.assertIn("Socket disconnected", out_text)
        self.assertNotIn("Traceback", out_text)

    def test_repl_history_file_persistence_and_loading(self) -> None:
        """Readline history file is initialized and saved at exit."""
        history_path = self.paths.history_file
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self.assertFalse(history_path.exists())

        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        with patch("builtins.input", side_effect=["salir"]):
            repl.run()

        # History file should exist (or readline save attempted)
        self.assertTrue(history_path.exists())


# ==============================================================================
# Group B: Fast-Path deterministic routing
# ==============================================================================

class TestFastPathDeterministicRouting(unittest.TestCase):
    """Test Fast-Path execution in REPL without LLM invocation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.paths = SiegfriedPaths(base_dir=Path(self.temp_dir.name))
        self.mock_client = MockIPCClient()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_fastpath_status_formatted_output(self) -> None:
        """'status' produces human-readable formatted summary rather than raw JSON."""
        self.mock_client.responses[IPCCommand.STATUS.value] = IPCResponse.ok(
            "1",
            {
                "state": "POMODORO_RUNNING",
                "remaining_seconds": 1250,
                "task_name": "Refactorizar REPL",
                "continuous_sitting_seconds": 1500,
                "total_duration_seconds": 1500,
                "timer_active": True,
                "audio_playing": False,
            },
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["status", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("POMODORO_RUNNING", out_text)
        self.assertIn("Refactorizar REPL", out_text)
        self.assertIn("20.8 min restantes de 25.0 min", out_text)
        self.assertIn("Tiempo sentado: 25.0 min", out_text)

    def test_fastpath_ping_command(self) -> None:
        """'ping' routes directly to IPCCommand.PING and reports state."""
        self.mock_client.responses[IPCCommand.PING.value] = IPCResponse.ok(
            "1",
            {"pong": True, "state": "IDLE"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["ping", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("pong", out_text.lower())
        self.assertIn("IDLE", out_text)

    def test_fastpath_bloque_command(self) -> None:
        """'bloque 30 Documentar' routes to IPCCommand.START_FOCUS with exact args."""
        self.mock_client.responses[IPCCommand.START_FOCUS.value] = IPCResponse.ok(
            "1",
            {"message": "Bloque de 30 min iniciado para Documentar"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["bloque 30 para Documentar", "salir"]):
            repl.run()
        start_reqs = [r for r in self.mock_client.sent_requests if r.cmd == IPCCommand.START_FOCUS.value]
        self.assertEqual(len(start_reqs), 1)
        self.assertEqual(start_reqs[0].args["duration_min"], 30)
        self.assertEqual(start_reqs[0].args["task"], "Documentar")

    def test_fastpath_ack_descanso_command(self) -> None:
        """'ack' and 'descanso' route to IPCCommand.ACK_BREAK."""
        self.mock_client.responses[IPCCommand.ACK_BREAK.value] = IPCResponse.ok(
            "1",
            {"message": "Descanso iniciado"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["ack", "descanso", "salir"]):
            repl.run()
        ack_reqs = [r for r in self.mock_client.sent_requests if r.cmd == IPCCommand.ACK_BREAK.value]
        self.assertEqual(len(ack_reqs), 2)

    def test_fastpath_cancelar_command(self) -> None:
        """'cancelar' routes to IPCCommand.CANCEL_FOCUS."""
        self.mock_client.responses[IPCCommand.CANCEL_FOCUS.value] = IPCResponse.ok(
            "1",
            {"message": "Bloque cancelado"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["cancelar", "salir"]):
            repl.run()
        cancel_reqs = [r for r in self.mock_client.sent_requests if r.cmd == IPCCommand.CANCEL_FOCUS.value]
        self.assertEqual(len(cancel_reqs), 1)

    def test_fastpath_posponer_command(self) -> None:
        """'posponer 5' routes to IPCCommand.POSTPONE."""
        self.mock_client.responses[IPCCommand.POSTPONE.value] = IPCResponse.ok(
            "1",
            {"message": "Pospuesto 5 minutos"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["posponer 5", "salir"]):
            repl.run()
        postpone_reqs = [r for r in self.mock_client.sent_requests if r.cmd == IPCCommand.POSTPONE.value]
        self.assertEqual(len(postpone_reqs), 1)
        self.assertEqual(postpone_reqs[0].args["minutes"], 5)

    def test_fastpath_ayuda_command(self) -> None:
        """'ayuda', 'help', and '?' output local command guide with ZERO IPC query calls."""
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        for h_cmd in ["ayuda", "help", "?"]:
            output = io.StringIO()
            with patch("sys.stdout", output), patch("builtins.input", side_effect=[h_cmd, "salir"]):
                repl.run()
            out_text = output.getvalue()
            self.assertIn("Comandos deterministas disponibles", out_text)
            self.assertIn("bloque", out_text)

        # Zero QUERY commands dispatched
        query_requests = [r for r in self.mock_client.sent_requests if r.cmd == IPCCommand.QUERY.value]
        self.assertEqual(len(query_requests), 0)

    def test_fastpath_zero_llm_invocation(self) -> None:
        """Ensure no cognitive/LLM calls occur during Fast-Path commands."""
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        fast_inputs = ["ping", "status", "cancelar", "ack", "salir"]
        with patch("sys.stdout", output), patch("builtins.input", side_effect=fast_inputs):
            repl.run()

        # All dispatches are pure IPC fast path, zero QUERY commands
        for req in self.mock_client.sent_requests:
            self.assertNotEqual(req.cmd, IPCCommand.QUERY.value)


# ==============================================================================
# Group C: Cognitive inference & backpressure
# ==============================================================================

class TestCognitiveInferenceAndBackpressure(unittest.TestCase):
    """Test cognitive route handling, backpressure notices, and user cancellation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.paths = SiegfriedPaths(base_dir=Path(self.temp_dir.name))
        self.mock_client = MockIPCClient()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_repl_cognitive_query_cloud_mock(self) -> None:
        """Cognitive response from Cloud engine displays response and route."""
        self.mock_client.responses[IPCCommand.QUERY.value] = IPCResponse.ok(
            "1",
            {"response": "Te sugiero descansar 5 minutos y estirar.", "route_used": "CLOUD"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["¿qué recomiendas?", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("Te sugiero descansar 5 minutos", out_text)
        self.assertIn("[CLOUD]", out_text)

    def test_repl_cognitive_query_local_mock(self) -> None:
        """Cognitive response from Local engine displays response and route."""
        self.mock_client.responses[IPCCommand.QUERY.value] = IPCResponse.ok(
            "1",
            {"response": "Bloque de trabajo completado con éxito.", "route_used": "LOCAL"},
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["resumen de bloque", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("Bloque de trabajo completado con éxito.", out_text)
        self.assertIn("[LOCAL]", out_text)

    def test_repl_inference_busy_backpressure_notice(self) -> None:
        """When daemon returns busy, REPL displays user-friendly busy notice."""
        self.mock_client.responses[IPCCommand.QUERY.value] = IPCResponse.busy(
            "1",
            reason="Inference workers busy (3/3)",
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["consulta intensiva", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("ocupado", out_text.lower())
        self.assertNotIn("Traceback", out_text)

    def test_repl_ctrl_c_during_query_cancels_client_wait(self) -> None:
        """Ctrl+C while awaiting response cancels wait cleanly without terminating REPL."""
        self.mock_client.raise_on_command[IPCCommand.QUERY.value] = KeyboardInterrupt()
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["pregunta larga", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("Consulta cancelada por el usuario", out_text)
        self.assertIn("Hasta luego", out_text)

    def test_repl_cognitive_query_fallback(self) -> None:
        """When fallback is used, response displays fallback tag."""
        self.mock_client.responses[IPCCommand.QUERY.value] = IPCResponse.ok(
            "1",
            {
                "response": "Respuesta generada tras fallback.",
                "route_used": "LOCAL",
                "fallback_used": True,
            },
        )
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["consulta con fallback", "salir"]):
            repl.run()
        out_text = output.getvalue()
        self.assertIn("Respuesta generada tras fallback.", out_text)
        self.assertIn("[LOCAL] (fallback)", out_text)

    def test_repl_local_only_zero_external_network(self) -> None:
        """Under LOCAL_ONLY policy, orchestrator never contacts Cloud engine."""
        cloud_mock = MockEngine("cloud", "Respuesta Cloud")
        local_mock = MockEngine("local", "Respuesta Local")
        orchestrator = InferenceOrchestrator(
            cloud_client=cloud_mock,
            local_client=local_mock,
            default_policy=InferencePolicy.LOCAL_ONLY,
        )
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Pregunta privada")])
        res = orchestrator.orchestrate(req, policy=InferencePolicy.LOCAL_ONLY)
        self.assertEqual(res.route_used, InferenceRoute.LOCAL)
        self.assertEqual(len(cloud_mock.calls), 0)
        self.assertEqual(len(local_mock.calls), 1)

    def test_repl_cloud_only_no_local_engine(self) -> None:
        """Under CLOUD_ONLY policy, orchestrator never invokes Local engine."""
        cloud_mock = MockEngine("cloud", "Respuesta Cloud")
        local_mock = MockEngine("local", "Respuesta Local")
        orchestrator = InferenceOrchestrator(
            cloud_client=cloud_mock,
            local_client=local_mock,
            default_policy=InferencePolicy.CLOUD_ONLY,
        )
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Consulta pública")])
        res = orchestrator.orchestrate(req, policy=InferencePolicy.CLOUD_ONLY)
        self.assertEqual(res.route_used, InferenceRoute.CLOUD)
        self.assertEqual(len(cloud_mock.calls), 1)
        self.assertEqual(len(local_mock.calls), 0)


# ==============================================================================
# Group D: Privacy, context & historical aggregator
# ==============================================================================

class TestPrivacyAndHistoricalAggregator(unittest.TestCase):
    """Test ephemeral context isolation, Vault integrity, and HistoricalAggregator."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.paths = SiegfriedPaths(base_dir=Path(self.temp_dir.name))
        self.mock_client = MockIPCClient()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_vault_no_chat_transcripts_written(self) -> None:
        """Vault JSONL contains only deterministic Event Schema v1 events, zero chat transcripts."""
        vault_file = self.paths.vault_file
        vault_file.parent.mkdir(parents=True, exist_ok=True)
        vault = Vault(vault_file)

        # Append typical domain events
        vault.append(Event.create(EventType.POMODORO_STARTED, {"duration_min": 25, "task": "Arquitectura"}))
        vault.append(Event.create(EventType.POMODORO_COMPLETED, {"duration_min": 25, "task": "Arquitectura"}))

        # Check file content
        with open(vault_file, "r", encoding="utf-8") as f:
            content = f.read()

        # No chat prompts or transcripts
        self.assertNotIn("¿cómo estás?", content)
        self.assertNotIn("InferenceMessage", content)
        self.assertNotIn("user_query", content)

    def test_vault_event_schema_v1_integrity(self) -> None:
        """All persisted events strictly conform to Event Schema v1."""
        vault_file = self.paths.vault_file
        vault_file.parent.mkdir(parents=True, exist_ok=True)
        vault = Vault(vault_file)

        vault.append(Event.create(EventType.POMODORO_STARTED, {"duration_min": 50, "task": "Core"}))
        vault.append(Event.create(EventType.BREAK_STARTED, {"duration_sec": 300}))

        events = list(vault.read_events())
        self.assertEqual(len(events), 2)
        for ev in events:
            self.assertEqual(ev.v, 1)
            self.assertGreater(ev.ts, 0)
            self.assertIn(ev.type, [t.value for t in EventType])
            self.assertIsInstance(ev.data, dict)

    def test_repl_ephemeral_context_retention_in_memory(self) -> None:
        """Context turns remain strictly in memory and are discarded on process exit."""
        repl = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        self.mock_client.responses[IPCCommand.QUERY.value] = IPCResponse.ok(
            "1",
            {"response": "Respuesta cognitiva", "route_used": "LOCAL"},
        )
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["Pregunta A", "salir"]):
            repl.run()

        # In-memory history has 2 messages
        self.assertEqual(len(repl.conversation_history), 2)

        # Re-instantiating a new REPL has 0 messages (ephemeral)
        repl_fresh = SiegfriedREPL(client=self.mock_client, paths=self.paths)
        self.assertEqual(len(repl_fresh.conversation_history), 0)

    def test_historical_aggregator_reverse_line_reader(self) -> None:
        """_reverse_line_reader reads lines backwards correctly regardless of chunk boundaries."""
        sample_file = self.paths.vault_file
        sample_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"line_{i}\n" for i in range(1000)]
        with open(sample_file, "w", encoding="utf-8") as f:
            f.writelines(lines)

        aggregator = HistoricalAggregator(vault_path=sample_file, buffer_size=128)
        read_lines = list(aggregator._reverse_line_reader())
        expected_reversed = [f"line_{i}" for i in range(999, -1, -1)]
        self.assertEqual(read_lines, expected_reversed)

    def test_historical_aggregator_time_filtering_early_break(self) -> None:
        """Aggregator breaks reverse parsing early when encountering ts < start_ts."""
        vault_file = self.paths.vault_file
        vault_file.parent.mkdir(parents=True, exist_ok=True)
        vault = Vault(vault_file)

        # Write 10 events: ts=100..109
        base_ts = 1700000000.0
        for i in range(10):
            vault.append(
                Event.create(
                    event_type=EventType.POMODORO_COMPLETED,
                    data={"duration_min": 25, "task": f"Task {i}"},
                    ts=base_ts + i * 60,
                )
            )

        aggregator = HistoricalAggregator(vault_path=vault_file)
        # Filter for only the last 3 events (ts >= base_ts + 7 * 60)
        start_ts = base_ts + 7 * 60 - 0.1
        end_ts = base_ts + 10 * 60

        metrics = aggregator.aggregate(start_ts=start_ts, end_ts=end_ts)
        self.assertEqual(metrics.completed_pomodoros, 3)

    def test_historical_aggregator_metrics_calculation(self) -> None:
        """Aggregator computes all health, focus, and posture metrics correctly."""
        vault_file = self.paths.vault_file
        vault_file.parent.mkdir(parents=True, exist_ok=True)
        vault = Vault(vault_file)

        base_ts = 1700000000.0
        # 1. Focus block A (25 min, Task Alfa)
        vault.append(Event.create(EventType.POMODORO_COMPLETED, {"duration_min": 25, "task": "Alfa"}, ts=base_ts))
        # 2. Break taken (5 min)
        vault.append(Event.create(EventType.BREAK_COMPLETED, {"duration_sec": 300}, ts=base_ts + 1500))
        # 3. Focus block B (20 min, Task Beta)
        vault.append(Event.create(EventType.POMODORO_COMPLETED, {"duration_min": 20, "task": "Beta"}, ts=base_ts + 1800))
        # 4. Postpone (5 min)
        vault.append(Event.create(EventType.POSTPONE_GRANTED, {"duration_min": 5}, ts=base_ts + 3000))
        # 5. Break interrupted (interrupted break)
        vault.append(Event.create(EventType.BREAK_INTERRUPTED, {"elapsed_seconds": 60}, ts=base_ts + 3300))
        # 6. Posture limit reached
        vault.append(Event.create(EventType.POSTURE_LIMIT_REACHED, {"sitting_seconds": 3600}, ts=base_ts + 3600))
        # 7. Posture warning
        vault.append(Event.create(EventType.POSTURE_WARNING, {"sitting_seconds": 3000}, ts=base_ts + 3700))

        aggregator = HistoricalAggregator(vault_path=vault_file)
        metrics = aggregator.aggregate(start_ts=base_ts - 10, end_ts=base_ts + 4000)

        self.assertEqual(metrics.total_focus_minutes, 45.0)
        self.assertEqual(metrics.completed_pomodoros, 2)
        self.assertEqual(metrics.completed_breaks, 1)
        self.assertEqual(metrics.interrupted_breaks, 1)
        self.assertEqual(metrics.postpones_granted, 1)
        self.assertEqual(metrics.posture_warnings, 1)
        self.assertEqual(metrics.posture_limits_reached, 1)
        self.assertEqual(metrics.by_task, {"Alfa": 25.0, "Beta": 20.0})

    def test_historical_aggregator_format_prompt_block(self) -> None:
        """Aggregator produces strict deterministic XML <metricas_historicas> tag."""
        metrics = AggregatedMetrics(
            total_focus_minutes=50.0,
            completed_pomodoros=2,
            completed_breaks=2,
            interrupted_breaks=0,
            postpones_granted=1,
            postpone_minutes=5.0,
            posture_warnings=1,
            posture_limits_reached=0,
            by_task={"Arquitectura": 50.0},
            events_analyzed=5,
        )
        aggregator = HistoricalAggregator(vault_path=self.paths.vault_file)
        xml_block = aggregator.format_prompt_block(metrics)
        self.assertTrue(xml_block.startswith("<metricas_historicas>"))
        self.assertTrue(xml_block.endswith("</metricas_historicas>"))
        self.assertIn("Minutos de enfoque totales: 50.0", xml_block)
        self.assertIn("Bloques de pomodoro completados: 2", xml_block)
        self.assertIn("- Arquitectura: 50.0 min", xml_block)

    def test_historical_aggregator_corrupt_lines_tolerance(self) -> None:
        """Aggregator ignores corrupt JSON lines and blank lines gracefully."""
        vault_file = self.paths.vault_file
        vault_file.parent.mkdir(parents=True, exist_ok=True)
        valid_ev = Event.create(EventType.POMODORO_COMPLETED, {"duration_min": 25.0, "task": "Valida"}, ts=1700000000.0)
        with open(vault_file, "w", encoding="utf-8") as f:
            f.write("\n")
            f.write("corrupted non json line\n")
            f.write(json.dumps(valid_ev.to_dict()) + "\n")
            f.write("{broken json\n")
            f.write("\n")

        aggregator = HistoricalAggregator(vault_path=vault_file)
        metrics = aggregator.aggregate(start_ts=0, end_ts=2000000000.0)
        self.assertEqual(metrics.completed_pomodoros, 1)
        self.assertEqual(metrics.total_focus_minutes, 25.0)

    def test_historical_aggregator_empty_file(self) -> None:
        """Empty or nonexistent vault file returns zeroed metrics cleanly."""
        missing_file = self.paths.base_dir / "does_not_exist.jsonl"
        aggregator = HistoricalAggregator(vault_path=missing_file)
        metrics = aggregator.aggregate(start_ts=0, end_ts=100)
        self.assertEqual(metrics.completed_pomodoros, 0)
        self.assertEqual(metrics.total_focus_minutes, 0.0)


# ==============================================================================
# Group E: Integration & E2E
# ==============================================================================

class TestReplIntegrationE2E(unittest.TestCase):
    """Real socket and daemon end-to-end tests for REPL interaction."""

    def setUp(self) -> None:
        from siegfried.storage.initialization import ensure_user_runtime
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.paths = SiegfriedPaths(
            base_dir=self.base_path / ".siegfried",
            runtime_dir=self.base_path / "run",
        )
        ensure_user_runtime(self.paths)
        self.daemon: Optional[SiegfriedDaemon] = None
        self.daemon_thread: Optional[threading.Thread] = None
        self._daemon_running = False

    def tearDown(self) -> None:
        self._daemon_running = False
        if self.daemon:
            self.daemon.stop()
            if self.daemon_thread:
                self.daemon_thread.join(timeout=3.0)
        self.temp_dir.cleanup()

    def _start_daemon(self) -> SiegfriedDaemon:
        self.daemon = SiegfriedDaemon(paths=self.paths)
        self.daemon.start()

        self._daemon_running = True
        def _loop() -> None:
            while self._daemon_running and self.daemon and self.daemon._running:
                try:
                    self.daemon.run_tick(timeout_seconds=0.02)
                except Exception:
                    break

        self.daemon_thread = threading.Thread(target=_loop, daemon=True)
        self.daemon_thread.start()

        # Wait for socket to be ready
        client = IPCClient(socket_path=self.paths.socket_file)
        start_t = time.monotonic()
        while time.monotonic() - start_t < 3.0:
            if self.paths.socket_file.exists():
                try:
                    resp = client.send_request(IPCRequest.create(IPCCommand.PING), timeout_seconds=0.5)
                    if resp.status == IPCStatus.OK.value:
                        return self.daemon
                except Exception:
                    pass
            time.sleep(0.05)
        raise RuntimeError("Daemon failed to become ready on socket")

    def test_e2e_repl_socket_daemon_fastpath(self) -> None:
        """Real REPL client communicating over Unix socket to real background SiegfriedDaemon."""
        self._start_daemon()
        client = IPCClient(socket_path=self.paths.socket_file)
        repl = SiegfriedREPL(client=client, paths=self.paths)

        output = io.StringIO()
        commands = ["ping", "status", "bloque 15 TareaE2E", "status", "cancelar", "status", "salir"]
        with patch("sys.stdout", output), patch("builtins.input", side_effect=commands):
            repl.run()

        out_text = output.getvalue()
        self.assertIn("pong", out_text.lower())
        self.assertIn("POMODORO_RUNNING", out_text)
        self.assertIn("IDLE", out_text)

    def test_e2e_repl_emergency_silence_enter_space(self) -> None:
        """Pressing Enter or Space during active alarm sends ACK_BREAK to silence alarm."""
        self._start_daemon()
        client = IPCClient(socket_path=self.paths.socket_file)

        # Force daemon state machine into CRITICAL_BREAK_REQUIRED
        self.daemon.state_machine._state = SystemState.CRITICAL_BREAK_REQUIRED
        self.assertEqual(self.daemon.state_machine.state, SystemState.CRITICAL_BREAK_REQUIRED)

        repl = SiegfriedREPL(client=client, paths=self.paths)
        output = io.StringIO()
        # Empty enter input should trigger emergency quick silence
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["", "salir"]):
            repl.run()

        out_text = output.getvalue()
        self.assertIn("Alarma silenciada", out_text)
        # Daemon state transitioned to BREAK_RUNNING
        self.assertEqual(self.daemon.state_machine.state, SystemState.BREAK_RUNNING)

    def test_e2e_repl_daemon_offline_graceful_notice(self) -> None:
        """REPL handles offline daemon with clear notification and non-blocking probe."""
        client = IPCClient(socket_path=self.paths.socket_file)
        repl = SiegfriedREPL(client=client, paths=self.paths)

        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["ping", "salir"]):
            repl.run()

        out_text = output.getvalue()
        self.assertIn("No se detectó comunicación con el daemon", out_text)
        self.assertIn("No se pudo conectar con el daemon", out_text)

    def test_e2e_repl_exit_does_not_terminate_daemon(self) -> None:
        """Exiting REPL leaves running daemon operational and accepting connections."""
        self._start_daemon()
        client = IPCClient(socket_path=self.paths.socket_file)
        repl = SiegfriedREPL(client=client, paths=self.paths)

        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["salir"]):
            repl.run()

        # Check daemon is still alive and responds to ping
        resp = client.send_request(IPCRequest.create(IPCCommand.PING), timeout_seconds=0.5)
        self.assertEqual(resp.status, IPCStatus.OK.value)
        self.assertTrue(resp.payload.get("pong"))

    def test_e2e_repl_no_lingering_threads(self) -> None:
        """Starting and stopping REPL leaves no orphan threads behind."""
        client = MockIPCClient()
        repl = SiegfriedREPL(client=client, paths=self.paths)

        threads_before = threading.active_count()
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", side_effect=["ayuda", "ping", "salir"]):
            repl.run()
        threads_after = threading.active_count()

        self.assertEqual(threads_before, threads_after)


if __name__ == "__main__":
    unittest.main()
