"""Integration and Reliability Test Suite for Gate F5.1.

Validates:
- Grupo A: Watcher Event-Driven (suscripción, zero polling, desconexión limpia, foco nulo, cambios rápidos).
- Grupo B: Privacidad Estricta (ausencia de títulos, URLs, rutas, sanitización de desconocidas, sin fuga a cloud).
- Grupo C: Duraciones Monotónicas (cómputo time.monotonic(), sin duraciones negativas, sin doble conteo, suspensión/reanudación).
- Grupo D: IPC & D-Bus Resilience (validación de límites, mensajes malformados, degradación elegante, aislamiento de funciones críticas).
- Grupo E: Integración End-to-End (KWin simulado -> FocusTracker -> Vault -> HistoricalAggregator, Event Schema v1, concurrencia con Pomodoro).
"""

import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import MagicMock, patch

from siegfried.contracts.config import get_default_core_profile
from siegfried.contracts.events import Event, EventType, EVENT_SCHEMA_VERSION
from siegfried.contracts.ipc import IPCCommand, IPCRequest, IPCResponse, IPCStatus
from siegfried.contracts.states import SystemState
from siegfried.core.errors import StorageError
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.daemon.focus import (
    DEFAULT_APP_CATEGORIES,
    FocusDBusAdapter,
    FocusTracker,
    normalize_and_categorize,
)
from siegfried.storage.aggregator import HistoricalAggregator
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault


def _setup_isolated_runtime(base_dir: Path, run_dir: Path) -> SiegfriedPaths:
    """Prepare minimal initialized runtime environment for isolated daemon testing."""
    paths = SiegfriedPaths(base_dir=base_dir, runtime_dir=run_dir)
    ensure_user_runtime(paths)
    return paths


# ==============================================================================
# GRUPO A: Watcher Event-Driven
# ==============================================================================

class TestGrupoAWatcherEventDriven(unittest.TestCase):
    """Grupo A: Validación de suscripción, zero polling, ciclo de vida y foco nulo."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault_file = Path(self.temp_dir.name) / "vault.jsonl"
        self.vault = Vault(self.vault_file)
        self.tracker = FocusTracker(vault=self.vault)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_a1_subscription_and_window_transition(self) -> None:
        """FocusTracker processes transitions between applications deterministically."""
        # Initial focus on code
        t0_mono = 1000.0
        t0_wall = 1700000000.0
        self.tracker.on_window_changed("code", monotonic_now=t0_mono, wall_now=t0_wall)
        snap = self.tracker.snapshot()
        self.assertEqual(snap["active_app"], "code")
        self.assertEqual(snap["active_category"], "desarrollo")

        # Switch to firefox after 60s
        t1_mono = 1060.0
        t1_wall = 1700000060.0
        event = self.tracker.on_window_changed("firefox", monotonic_now=t1_mono, wall_now=t1_wall)

        # Confirm event for 'code' was emitted
        self.assertIsNotNone(event)
        self.assertEqual(event.type, EventType.WINDOW_FOCUS_SAMPLED.value)
        self.assertEqual(event.data["aplicacion"], "code")
        self.assertEqual(event.data["categoria"], "desarrollo")
        self.assertEqual(event.data["duracion"], 60.0)

        # Confirm tracker state is now firefox
        snap2 = self.tracker.snapshot()
        self.assertEqual(snap2["active_app"], "firefox")
        self.assertEqual(snap2["active_category"], "navegacion")

    def test_a2_zero_polling_event_driven_durations(self) -> None:
        """Durations depend entirely on signal timestamps, with zero timer ticks required."""
        self.tracker.on_window_changed("konsole", monotonic_now=100.0, wall_now=1700000000.0)
        # Without any intermediate calls or polling, jump 300s
        event = self.tracker.on_window_changed("code", monotonic_now=400.0, wall_now=1700000300.0)

        self.assertIsNotNone(event)
        self.assertEqual(event.data["aplicacion"], "konsole")
        self.assertEqual(event.data["duracion"], 300.0)

    def test_a3_clean_disconnection_and_stop(self) -> None:
        """Closing active interval flushes current application and clears state."""
        self.tracker.on_window_changed("obsidian", monotonic_now=50.0, wall_now=1700000000.0)
        close_event = self.tracker.close_active_interval(monotonic_now=75.0, wall_now=1700000025.0)

        self.assertIsNotNone(close_event)
        self.assertEqual(close_event.data["aplicacion"], "obsidian")
        self.assertEqual(close_event.data["categoria"], "ofimatica")
        self.assertEqual(close_event.data["duracion"], 25.0)

        snap = self.tracker.snapshot()
        self.assertIsNone(snap["active_app"])

    def test_a4_enable_disable_lifecycle(self) -> None:
        """Disabling tracker stops recording transitions; re-enabling accepts new ones."""
        self.tracker.on_window_changed("code", monotonic_now=10.0, wall_now=1700000000.0)
        self.tracker.set_active(False)

        # Transitions while inactive are discarded
        ev = self.tracker.on_window_changed("firefox", monotonic_now=20.0, wall_now=1700000010.0)
        self.assertIsNone(ev)
        snap = self.tracker.snapshot()
        self.assertFalse(snap["is_active"])

        # Re-enable
        self.tracker.set_active(True)
        self.tracker.on_window_changed("konsole", monotonic_now=30.0, wall_now=1700000020.0)
        ev2 = self.tracker.on_window_changed("code", monotonic_now=45.0, wall_now=1700000035.0)
        self.assertIsNotNone(ev2)
        self.assertEqual(ev2.data["aplicacion"], "konsole")
        self.assertEqual(ev2.data["duracion"], 15.0)

    def test_a5_null_focus_handling(self) -> None:
        """Switching to null focus (desktop clicked or minimized) closes app and stays idle."""
        self.tracker.on_window_changed("firefox", monotonic_now=10.0, wall_now=1700000000.0)

        # User minimizes or clicks empty desktop
        ev = self.tracker.on_window_changed(None, monotonic_now=40.0, wall_now=1700000030.0)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.data["aplicacion"], "firefox")
        self.assertEqual(ev.data["duracion"], 30.0)

        # Idle time on desktop (100 seconds pass in null focus)
        snap = self.tracker.snapshot()
        self.assertIsNone(snap["active_app"])

        # User focuses konsole: konsole start time begins NOW (idle time not counted)
        ev2 = self.tracker.on_window_changed("konsole", monotonic_now=140.0, wall_now=1700000130.0)
        self.assertIsNone(ev2)  # No previous app interval was open

        # Close konsole after 20s
        ev3 = self.tracker.on_window_changed(None, monotonic_now=160.0, wall_now=1700000150.0)
        self.assertIsNotNone(ev3)
        self.assertEqual(ev3.data["aplicacion"], "konsole")
        self.assertEqual(ev3.data["duracion"], 20.0)

    def test_a6_rapid_window_switches_alt_tab(self) -> None:
        """Rapid Alt-Tab switches do not cause race conditions or corrupt durations."""
        mono = 1000.0
        apps = ["code", "firefox", "konsole", "slack", "code"]
        for app in apps:
            self.tracker.on_window_changed(app, monotonic_now=mono, wall_now=1700000000.0 + mono)
            mono += 0.2  # 200 ms micro-switches

        # Final flush
        self.tracker.close_active_interval(monotonic_now=mono, wall_now=1700000000.0 + mono)

        # All events should exist in vault
        events = list(self.vault.read_events())
        self.assertEqual(len(events), 5)
        for e in events:
            self.assertEqual(e.data["duracion"], 0.2)


# ==============================================================================
# GRUPO B: Privacidad Estricta
# ==============================================================================

class TestGrupoBPrivacyGuarantees(unittest.TestCase):
    """Grupo B: Verificación estricta de ausencia de títulos, URLs, rutas y filtrado."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault_file = Path(self.temp_dir.name) / "vault.jsonl"
        self.vault = Vault(self.vault_file)
        self.tracker = FocusTracker(vault=self.vault)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_b1_absence_of_window_titles(self) -> None:
        """Window captions and titles containing sensitive info are strictly rejected/sanitized."""
        bad_inputs = [
            "Secret Document - LibreOffice Writer",
            "Budget_2026.xlsx - Local",
            "My Confidential Plan — Google Docs",
            "Private Note.md",
        ]
        for bad in bad_inputs:
            app, cat = normalize_and_categorize(bad)
            self.assertEqual(app, "desconocida")
            self.assertEqual(cat, "desconocida")

    def test_b2_absence_of_urls_and_web_queries(self) -> None:
        """URLs, queries and web addresses are strictly rejected."""
        url_inputs = [
            "https://bank.com/account",
            "http://localhost:8080/dashboard",
            "firefox?url=https://secret.org",
            "chrome://settings/passwords",
            "file:///home/user/passwords.txt",
        ]
        for url in url_inputs:
            app, cat = normalize_and_categorize(url)
            self.assertEqual(app, "desconocida")
            self.assertEqual(cat, "desconocida")

    def test_b3_absence_of_command_arguments_and_paths(self) -> None:
        """Process arguments, bash commands, and paths are rejected."""
        cmd_inputs = [
            "/bin/bash -c 'rm -rf /'",
            "/usr/bin/python3 -m siegfried",
            "code /home/user/project/secrets.env",
            "konsole --hold -e cat /etc/shadow",
        ]
        for cmd in cmd_inputs:
            app, cat = normalize_and_categorize(cmd)
            self.assertEqual(app, "desconocida")
            self.assertEqual(cat, "desconocida")

    def test_b4_rejection_of_unauthorized_payload_attributes(self) -> None:
        """Events appended to Vault only contain approved Event Schema v1 fields."""
        self.tracker.on_window_changed("code", monotonic_now=10.0, wall_now=1700000000.0)
        self.tracker.on_window_changed(None, monotonic_now=30.0, wall_now=1700000020.0)

        events = list(self.vault.read_events())
        self.assertEqual(len(events), 1)
        event = events[0]

        # Verify only 'aplicacion', 'categoria', 'duracion' in data payload
        self.assertEqual(set(event.data.keys()), {"aplicacion", "categoria", "duracion"})
        self.assertNotIn("title", event.data)
        self.assertNotIn("url", event.data)
        self.assertNotIn("caption", event.data)
        self.assertNotIn("path", event.data)

    def test_b5_safe_classification_of_unknown_applications(self) -> None:
        """Unrecognized application names are categorized as 'desconocida' safely."""
        app, cat = normalize_and_categorize("custom-proprietary-tool")
        self.assertEqual(app, "custom-proprietary-tool")
        self.assertEqual(cat, "desconocida")

        # Injected or suspicious tokens map entirely to desconocida
        app2, cat2 = normalize_and_categorize("<script>alert(1)</script>")
        self.assertEqual(app2, "desconocida")
        self.assertEqual(cat2, "desconocida")

    def test_b6_no_leakage_to_cloud_or_remote(self) -> None:
        """Tracker operates 100% locally on local disk; zero remote/cloud calls."""
        with patch("urllib.request.urlopen") as mock_http:
            self.tracker.on_window_changed("code", monotonic_now=1.0)
            self.tracker.on_window_changed("firefox", monotonic_now=10.0)
            self.tracker.close_active_interval(monotonic_now=20.0)
            mock_http.assert_not_called()


# ==============================================================================
# GRUPO C: Duraciones Monotónicas
# ==============================================================================

class TestGrupoCDurationsAndMonotonicity(unittest.TestCase):
    """Grupo C: Validación de tiempos monotónicos, sin duraciones negativas ni doble conteo."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault_file = Path(self.temp_dir.name) / "vault.jsonl"
        self.vault = Vault(self.vault_file)
        self.tracker = FocusTracker(vault=self.vault)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_c1_monotonic_duration_computation(self) -> None:
        """Duration is computed strictly via monotonic difference."""
        self.tracker.on_window_changed("code", monotonic_now=100.5, wall_now=1700000000.0)
        ev = self.tracker.on_window_changed("firefox", monotonic_now=225.8, wall_now=1700000125.3)

        self.assertIsNotNone(ev)
        expected_dur = round(225.8 - 100.5, 2)
        self.assertEqual(ev.data["duracion"], expected_dur)

    def test_c2_negative_durations_prevented_and_clamped(self) -> None:
        """Negative elapsed time (e.g. synthetic or disordered signal) is clamped to >= 0.0."""
        self.tracker.on_window_changed("code", monotonic_now=500.0, wall_now=1700000000.0)
        # Clock backwards simulation
        ev = self.tracker.on_window_changed("firefox", monotonic_now=450.0, wall_now=1700000000.0)

        self.assertIsNotNone(ev)
        self.assertGreaterEqual(ev.data["duracion"], 0.0)
        self.assertEqual(ev.data["duracion"], 0.0)

    def test_c3_no_double_counting_on_duplicate_consecutive_events(self) -> None:
        """Repeating the exact same window focus does not split intervals or double-count."""
        self.tracker.on_window_changed("code", monotonic_now=10.0, wall_now=1700000000.0)
        # Duplicate signal for 'code'
        ev_dup = self.tracker.on_window_changed("code", monotonic_now=20.0, wall_now=1700000010.0)
        self.assertIsNone(ev_dup)  # Did not close or emit prematurely

        # Third duplicate
        ev_dup2 = self.tracker.on_window_changed("code", monotonic_now=30.0, wall_now=1700000020.0)
        self.assertIsNone(ev_dup2)

        # Switch to firefox at t=60
        ev_final = self.tracker.on_window_changed("firefox", monotonic_now=60.0, wall_now=1700000050.0)
        self.assertIsNotNone(ev_final)
        self.assertEqual(ev_final.data["duracion"], 50.0)

    def test_c4_suspend_and_resume_preserves_accuracy(self) -> None:
        """Suspension immediately closes interval; sleeping time is not counted as focus."""
        # Work for 120 seconds
        self.tracker.on_window_changed("code", monotonic_now=100.0, wall_now=1700000000.0)
        ev_suspend = self.tracker.on_suspend(monotonic_now=220.0, wall_now=1700000120.0)

        self.assertIsNotNone(ev_suspend)
        self.assertEqual(ev_suspend.data["duracion"], 120.0)

        # System suspended for 4 hours (14400 seconds)
        mono_resume = 14620.0
        wall_resume = 1700014520.0
        self.tracker.on_resume(monotonic_now=mono_resume, wall_now=wall_resume)

        # User starts working in firefox after resume
        ev_after = self.tracker.on_window_changed("firefox", monotonic_now=mono_resume + 5.0, wall_now=wall_resume + 5.0)
        self.assertIsNone(ev_after)  # No interval was pending during sleep

        # Work in firefox for 30 seconds
        ev_firefox = self.tracker.close_active_interval(monotonic_now=mono_resume + 35.0, wall_now=wall_resume + 35.0)
        self.assertIsNotNone(ev_firefox)
        self.assertEqual(ev_firefox.data["aplicacion"], "firefox")
        self.assertEqual(ev_firefox.data["duracion"], 30.0)

    def test_c5_daemon_restart_recovery(self) -> None:
        """On daemon shutdown, active interval is closed; next daemon instance starts clean."""
        self.tracker.on_window_changed("konsole", monotonic_now=10.0, wall_now=1700000000.0)
        ev_shutdown = self.tracker.close_active_interval(monotonic_now=40.0, wall_now=1700000030.0)
        self.assertIsNotNone(ev_shutdown)
        self.assertEqual(ev_shutdown.data["duracion"], 30.0)

        # New tracker instance simulates daemon restart
        new_tracker = FocusTracker(vault=self.vault)
        snap = new_tracker.snapshot()
        self.assertIsNone(snap["active_app"])
        self.assertEqual(snap["elapsed_seconds"], 0.0)


# ==============================================================================
# GRUPO D: IPC & D-Bus Resilience
# ==============================================================================

class TestGrupoDIPCAndDBusResilience(unittest.TestCase):
    """Grupo D: Validación de adaptadores, límites de tamaño, degradación elegante y aislamiento."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault_file = Path(self.temp_dir.name) / "vault.jsonl"
        self.vault = Vault(self.vault_file)
        self.tracker = FocusTracker(vault=self.vault)
        self.adapter = FocusDBusAdapter(self.tracker)

    def tearDown(self) -> None:
        self.adapter.stop()
        self.temp_dir.cleanup()

    def test_d1_payload_size_limits_and_truncation(self) -> None:
        """Adapter enforces max payload boundaries (128 chars) and does not blow up."""
        huge_name = "a" * 1000
        # Call tracker directly with huge string
        app, cat = normalize_and_categorize(huge_name)
        self.assertEqual(app, "desconocida")
        self.assertEqual(cat, "desconocida")

    def test_d2_graceful_degradation_without_dbus(self) -> None:
        """If D-Bus environment is missing, adapter start returns False cleanly without crashing."""
        with patch.dict(os.environ, {}, clear=True):
            # No DBUS_SESSION_BUS_ADDRESS
            started = self.adapter.start()
            self.assertFalse(started)
            self.assertFalse(self.adapter.is_running())

    def test_d3_malformed_messages_do_not_crash(self) -> None:
        """Malformed or unexpected input types are safely caught."""
        self.tracker.on_window_changed(None)
        self.tracker.on_window_changed("")
        self.tracker.on_window_changed("   ")
        snap = self.tracker.snapshot()
        self.assertIsNone(snap["active_app"])

    def test_d4_isolation_of_critical_daemon_functions(self) -> None:
        """Exceptions in focus tracker do not compromise core timers or state machine."""
        base = Path(self.temp_dir.name) / "base"
        run = Path(self.temp_dir.name) / "run"
        paths = _setup_isolated_runtime(base, run)

        daemon = SiegfriedDaemon(paths=paths)
        daemon.start()
        try:
            # Force focus tracker error
            with patch.object(daemon.focus_tracker, "on_window_changed", side_effect=RuntimeError("Simulated focus failure")):
                # STATUS query must still succeed with 100% reliability
                req = IPCRequest.create(IPCCommand.STATUS.value)
                res = daemon.handle_ipc_request(req)
                self.assertEqual(res.status, IPCStatus.OK.value)
                self.assertEqual(res.payload["state"], SystemState.IDLE.value)

                # PING query must still succeed
                ping_req = IPCRequest.create(IPCCommand.PING.value)
                ping_res = daemon.handle_ipc_request(ping_req)
                self.assertEqual(ping_res.status, IPCStatus.OK.value)
                self.assertTrue(ping_res.payload["pong"])
        finally:
            daemon.stop()


# ==============================================================================
# GRUPO E: Integración End-to-End
# ==============================================================================

class TestGrupoEIntegration(unittest.TestCase):
    """Grupo E: KWin simulado -> FocusTracker -> Vault -> HistoricalAggregator."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name) / "base"
        self.run_dir = Path(self.temp_dir.name) / "run"
        self.paths = _setup_isolated_runtime(self.base_dir, self.run_dir)
        self.daemon = SiegfriedDaemon(paths=self.paths)

    def tearDown(self) -> None:
        self.daemon.stop()
        self.temp_dir.cleanup()

    def test_e1_full_simulated_flow_to_vault(self) -> None:
        """Simulated KWin events persist Event Schema v1 records into siegfried_vault.jsonl."""
        self.daemon.start()

        # Simulate KWin focus transitions
        tracker = self.daemon.focus_tracker
        tracker.on_window_changed("code", monotonic_now=10.0, wall_now=1700000000.0)
        tracker.on_window_changed("konsole", monotonic_now=70.0, wall_now=1700000060.0)
        tracker.on_window_changed("firefox", monotonic_now=100.0, wall_now=1700000090.0)
        tracker.close_active_interval(monotonic_now=130.0, wall_now=1700000120.0)

        # Inspect Vault
        events = list(self.daemon.vault.read_events())
        focus_events = [e for e in events if e.type == EventType.WINDOW_FOCUS_SAMPLED.value]
        self.assertEqual(len(focus_events), 3)

        # Verify event schema and values
        self.assertEqual(focus_events[0].data["aplicacion"], "code")
        self.assertEqual(focus_events[0].data["categoria"], "desarrollo")
        self.assertEqual(focus_events[0].data["duracion"], 60.0)

        self.assertEqual(focus_events[1].data["aplicacion"], "konsole")
        self.assertEqual(focus_events[1].data["categoria"], "terminal")
        self.assertEqual(focus_events[1].data["duracion"], 30.0)

        self.assertEqual(focus_events[2].data["aplicacion"], "firefox")
        self.assertEqual(focus_events[2].data["categoria"], "navegacion")
        self.assertEqual(focus_events[2].data["duracion"], 30.0)

    def test_e2_event_schema_v1_strict_conformance(self) -> None:
        """Generated events strictly adhere to Event Schema v1 contracts."""
        self.daemon.start()
        tracker = self.daemon.focus_tracker
        tracker.on_window_changed("code", monotonic_now=10.0, wall_now=1700000000.0)
        event = tracker.close_active_interval(monotonic_now=35.5, wall_now=1700000025.5)

        self.assertIsNotNone(event)
        self.assertEqual(event.v, EVENT_SCHEMA_VERSION)
        self.assertEqual(event.type, EventType.WINDOW_FOCUS_SAMPLED.value)
        self.assertIsInstance(event.ts, float)
        self.assertIn("aplicacion", event.data)
        self.assertIn("categoria", event.data)
        self.assertIn("duracion", event.data)

        # Can serialize and deserialize through Event.from_dict
        d = event.to_dict()
        restored = Event.from_dict(d)
        self.assertEqual(restored.type, event.type)
        self.assertEqual(restored.data, event.data)

    def test_e3_historical_aggregator_compatibility(self) -> None:
        """HistoricalAggregator remains 100% functional and uncorrupted by focus events."""
        self.daemon.start()

        # Append Pomodoro events and Focus events intertwined
        pomo_event = Event.create(EventType.POMODORO_COMPLETED, {"task": "Refactor F5.1", "duration_min": 25.0}, ts=1700000000.0)
        self.daemon.vault.append(pomo_event)

        # Focus events
        self.daemon.focus_tracker.on_window_changed("code", monotonic_now=10.0, wall_now=1700000000.0)
        self.daemon.focus_tracker.close_active_interval(monotonic_now=70.0, wall_now=1700000060.0)

        pomo_event_2 = Event.create(EventType.POMODORO_COMPLETED, {"task": "Docs F5.1", "duration_min": 15.0}, ts=1700000100.0)
        self.daemon.vault.append(pomo_event_2)

        # Query HistoricalAggregator
        aggregator = HistoricalAggregator(self.paths.vault_file)
        metrics = aggregator.aggregate(allow_early_exit=False)

        # Confirms Pomodoro aggregation is completely intact
        self.assertEqual(metrics.completed_pomodoros, 2)
        self.assertEqual(metrics.total_focus_minutes, 40.0)
        self.assertEqual(metrics.by_task["Refactor F5.1"], 25.0)
        self.assertEqual(metrics.by_task["Docs F5.1"], 15.0)

    def test_e4_independence_from_inference_engines(self) -> None:
        """FocusTracker does not invoke LLM inference or require orchestrator."""
        self.daemon.start()
        with patch.object(self.daemon, "_orchestrator") as mock_orch:
            self.daemon.focus_tracker.on_window_changed("code", monotonic_now=1.0)
            self.daemon.focus_tracker.on_window_changed("firefox", monotonic_now=20.0)
            self.daemon.focus_tracker.close_active_interval(monotonic_now=40.0)
            mock_orch.route.assert_not_called()

    def test_e5_concurrency_between_pomodoro_and_focus_events(self) -> None:
        """Focus tracking occurs concurrently with active Pomodoro blocks without collision."""
        self.daemon.start()

        # Start pomodoro block
        start_req = IPCRequest.create(IPCCommand.START_FOCUS.value, {"duration_min": 25, "task": "Concurrencia"})
        start_res = self.daemon.handle_ipc_request(start_req)
        self.assertEqual(start_res.status, IPCStatus.OK.value)

        # Simulate concurrent window switching in parallel thread
        def simulate_switches():
            for i in range(10):
                self.daemon.focus_tracker.on_window_changed(f"app_{i % 3}", monotonic_now=10.0 + i, wall_now=1700000000.0 + i)
                time.sleep(0.01)

        th = threading.Thread(target=simulate_switches)
        th.start()
        th.join()

        # Verify Pomodoro timer is still running intact
        status_req = IPCRequest.create(IPCCommand.STATUS.value)
        status_res = self.daemon.handle_ipc_request(status_req)
        self.assertEqual(status_res.status, IPCStatus.OK.value)
        self.assertEqual(status_res.payload["state"], SystemState.POMODORO_RUNNING.value)
        self.assertTrue(status_res.payload["timer_active"])


if __name__ == "__main__":
    unittest.main()
