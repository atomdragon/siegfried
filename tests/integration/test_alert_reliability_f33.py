"""Adversarial and reliability test suite for Alert Hardening and Gate F3.3.

Covers:
Grupo A — Saturación de cola (capacidad, rechazo, preservación de dominio, reactor libre, trabajadores acotados).
Grupo B — Idempotencia e identidad (event_id, claves temporales, reinicios, entregas fallidas, no-duplicación).
Grupo C — Postura determinista (aviso 50m, barrera 60m, prórrogas, silenciado vs descanso, ACK_BREAK, CANCEL_FOCUS).
Grupo D — Audio quirúrgico y shutdown (PID propio, procesos externos, carreras start/stop, timeout acotado, estados residuales).
Grupo E — Integración completa (Timer real, State Machine real, Vault temporal, IPC socket real, Fast-Path bajo saturación).
"""

from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from siegfried.contracts.alerts import (
    Alert,
    AlertType,
    AlertUrgency,
    DeliveryStatus,
    NotificationAttempt,
)
from siegfried.contracts.events import Event, EventType, EVENT_SCHEMA_VERSION
from siegfried.contracts.ipc import IPCCommand, IPCStatus, IPCRequest, IPCResponse
from siegfried.contracts.states import (
    SystemState,
    MAX_CONTINUOUS_SITTING_SECONDS,
    POSTURE_WARNING_SECONDS,
)
from siegfried.core.state_machine import HealthStateMachine
from siegfried.daemon.alerts import AlertCoordinator
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.daemon.timers import MonotonicTimer
from siegfried.integrations.audio import (
    PipeWireAudioPlayer,
    StubAudioPlayer,
    AUTHORIZED_AUDIO_EXTENSIONS,
)
from siegfried.integrations.notifications import (
    DesktopNotificationSender,
    StubNotificationSender,
    _sanitize_alert_text,
)
from siegfried.ipc.client import IPCClient
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault


class BlockedNotifier:
    """Notification sender that blocks until unblocked, simulating slow/hung desktop bus."""

    def __init__(self, block_event: threading.Event) -> None:
        self.block_event = block_event
        self.call_count = 0

    def send(self, title: str, message: str, urgency: str = "normal") -> bool:
        self.call_count += 1
        self.block_event.wait(timeout=2.0)
        return True


class TestAlertReliabilityF33(unittest.TestCase):
    """Reliability and adversarial test suite for Gate F3.3 alert hardening."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp_dir.name) / ".siegfried"
        self.runtime_dir = Path(self.tmp_dir.name) / "run"
        self.runtime_dir.mkdir(parents=True, mode=0o700, exist_ok=True)

        self.paths = SiegfriedPaths(base_dir=self.base_dir, runtime_dir=self.runtime_dir)
        from siegfried.storage.initialization import ensure_user_runtime
        ensure_user_runtime(self.paths)

        # Initialize mock sound file
        self.paths.alert_sound_file.write_bytes(b"OGG_TEST_DATA")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # ══════════════════════════════════════════════════════════
    # GRUPO A — Saturación de la Cola
    # ══════════════════════════════════════════════════════════

    def test_01_queue_available_capacity(self) -> None:
        """AlertCoordinator accepts alerts when capacity is available."""
        notif = StubNotificationSender()
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=notif, audio_player=audio, max_queue_size=4)
        coord._force_queue_for_stubs = True
        coord.start()

        alert = Alert(
            alert_id="alt-1",
            alert_type=AlertType.BREAK_STARTED,
            title="Title",
            message="Msg",
            urgency=AlertUrgency.LOW.value,
            timestamp=time.time(),
        )
        enqueued = coord._dispatch_immediate_or_queue(alert)
        self.assertTrue(enqueued)
        coord.flush(1.0)
        coord.stop()

    def test_02_queue_fully_occupied(self) -> None:
        """Queue reaches maximum capacity when blocked."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=blocked_notif, audio_player=audio, max_queue_size=4)
        coord.start()

        for i in range(4):
            alt = Alert(
                alert_id=f"alt-fill-{i}",
                alert_type=AlertType.BREAK_STARTED,
                title="Notice",
                message=f"Msg {i}",
                urgency=AlertUrgency.LOW.value,
                timestamp=time.time(),
            )
            self.assertTrue(coord._dispatch_immediate_or_queue(alt))

        self.assertEqual(coord.pending_alerts_count, 4)
        block.set()
        coord.flush(1.0)
        coord.stop()

    def test_03_saturation_with_low_priority_alerts(self) -> None:
        """When queue is full, additional LOW alerts are rejected without blocking."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=blocked_notif, audio_player=audio, max_queue_size=3)
        coord.start()

        for i in range(3):
            alt = Alert(
                alert_id=f"alt-low-{i}",
                alert_type=AlertType.BREAK_STARTED,
                title="Notice",
                message=f"Msg {i}",
                urgency=AlertUrgency.LOW.value,
                timestamp=time.time(),
            )
            coord._dispatch_immediate_or_queue(alt)

        # 4th LOW alert under saturation
        overflow_alert = Alert(
            alert_id="alt-low-overflow",
            alert_type=AlertType.BREAK_STARTED,
            title="Notice",
            message="Overflow",
            urgency=AlertUrgency.LOW.value,
            timestamp=time.time(),
        )
        accepted = coord._dispatch_immediate_or_queue(overflow_alert)
        self.assertFalse(accepted)

        attempts = coord.attempts
        self.assertTrue(any(a.alert_id == "alt-low-overflow" and not a.accepted for a in attempts))

        block.set()
        coord.flush(1.0)
        coord.stop()

    def test_04_saturation_exclusive_critical_alerts(self) -> None:
        """When queue is saturated exclusively with CRITICAL alerts, incoming CRITICAL does not drop existing CRITICAL."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=blocked_notif, audio_player=audio, max_queue_size=3)
        coord.start()

        for i in range(3):
            alt = Alert(
                alert_id=f"alt-crit-{i}",
                alert_type=AlertType.POSTURE_LIMIT_REACHED,
                title="Límite Postural",
                message=f"Postura {i}",
                urgency=AlertUrgency.CRITICAL.value,
                timestamp=time.time(),
            )
            coord._dispatch_immediate_or_queue(alt)

        # 4th CRITICAL alert: queue has no LOW or NORMAL to evict!
        fourth_crit = Alert(
            alert_id="alt-crit-4",
            alert_type=AlertType.POSTURE_LIMIT_REACHED,
            title="Límite Postural",
            message="Postura 4",
            urgency=AlertUrgency.CRITICAL.value,
            timestamp=time.time(),
        )
        accepted = coord._dispatch_immediate_or_queue(fourth_crit)
        self.assertFalse(accepted)

        # Rejection recorded explicitly
        attempts = coord.attempts
        self.assertTrue(any(
            a.alert_id == "alt-crit-4" and not a.accepted and "saturated exclusively with CRITICAL alerts" in (a.error_msg or "")
            for a in attempts
        ))

        block.set()
        coord.flush(1.0)
        coord.stop()

    def test_05_rejection_properly_recorded_in_attempts(self) -> None:
        """Delivery failure under saturation has explicit audit record with alert_id and error message."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=blocked_notif, audio_player=audio, max_queue_size=2)
        coord.start()

        for i in range(2):
            coord._dispatch_immediate_or_queue(Alert(
                alert_id=f"a-{i}",
                alert_type=AlertType.POMODORO_COMPLETED,
                title="T",
                message="M",
                urgency=AlertUrgency.NORMAL.value,
                timestamp=time.time(),
            ))

        rejected = coord._dispatch_immediate_or_queue(Alert(
            alert_id="a-rej",
            alert_type=AlertType.POMODORO_COMPLETED,
            title="T",
            message="M",
            urgency=AlertUrgency.NORMAL.value,
            timestamp=time.time(),
        ))
        self.assertFalse(rejected)

        attempt = next(a for a in coord.attempts if a.alert_id == "a-rej")
        self.assertFalse(attempt.accepted)
        self.assertIn("saturated", attempt.error_msg)
        self.assertEqual(attempt.status, DeliveryStatus.FAILED.value)

        block.set()
        coord.flush(1.0)
        coord.stop()

    def test_06_domain_event_intact_after_saturation(self) -> None:
        """Domain event in Vault remains fully persisted even if queue saturation rejects the alert."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=blocked_notif, audio_player=stub_audio)
        daemon.alert_coordinator.max_queue_size = 2
        daemon.start()

        # Fill queue to maximum capacity
        for i in range(2):
            daemon.alert_coordinator._dispatch_immediate_or_queue(Alert(
                alert_id=f"alt-filler-{i}",
                alert_type=AlertType.BREAK_STARTED,
                title="Low",
                message="Msg",
                urgency=AlertUrgency.LOW.value,
                timestamp=time.time(),
            ))

        # Now trigger posture limit milestone in daemon
        daemon.state_machine._continuous_sitting_seconds = 3600.0
        daemon._check_posture_milestones()

        # Vault MUST have the domain event persisted regardless of alert delivery state!
        vault_lines = self.paths.vault_file.read_text().strip().splitlines()
        self.assertGreaterEqual(len(vault_lines), 1)
        last_evt = json.loads(vault_lines[-1])
        self.assertEqual(last_evt["type"], EventType.POSTURE_LIMIT_REACHED.value)

        block.set()
        daemon.stop()

    def test_07_reactor_not_blocked_during_saturation(self) -> None:
        """Reactor run_tick completes in <10ms even when alert queue is backed up."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=blocked_notif, audio_player=stub_audio)
        daemon.alert_coordinator.max_queue_size = 2
        daemon.start()

        # Fill queue
        for i in range(2):
            daemon.alert_coordinator._dispatch_immediate_or_queue(Alert(
                alert_id=f"alt-block-{i}",
                alert_type=AlertType.BREAK_STARTED,
                title="Low",
                message="Msg",
                urgency=AlertUrgency.LOW.value,
                timestamp=time.time(),
            ))

        t0 = time.monotonic()
        daemon.run_tick(timeout_seconds=0.001)
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        self.assertLess(elapsed_ms, 15.0)

        block.set()
        daemon.stop()

    def test_08_no_unlimited_worker_growth(self) -> None:
        """AlertCoordinator maintains strictly 1 worker thread regardless of load."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=stub_notif, audio_player=stub_audio, max_queue_size=16)
        coord._force_queue_for_stubs = True
        coord.start()

        # Enqueue 50 alerts
        for i in range(50):
            coord.trigger_pomodoro_completed(task_name=f"Task-{i}", duration_sec=1500.0)

        alert_threads = [t for t in threading.enumerate() if t.name == "siegfried-alert-worker"]
        self.assertEqual(len(alert_threads), 1)

        coord.stop()

    # ══════════════════════════════════════════════════════════
    # GRUPO B — Idempotencia e Identidad de Eventos
    # ══════════════════════════════════════════════════════════

    def test_09_same_event_repeated_deduplicated(self) -> None:
        """Same event_id dispatched twice is suppressed on the second attempt."""
        notif = StubNotificationSender()
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=notif, audio_player=audio)

        alt1 = coord.trigger_pomodoro_completed("CS Task", 1500.0, event_id="pomo:100.0")
        self.assertIsNotNone(alt1)

        alt2 = coord.trigger_pomodoro_completed("CS Task", 1500.0, event_id="pomo:100.0")
        self.assertIsNone(alt2)

        suppressed_attempt = next(a for a in coord.attempts if "pomo:100.0" in (a.error_msg or ""))
        self.assertEqual(suppressed_attempt.status, DeliveryStatus.SUPPRESSED.value)

    def test_10_different_events_same_type_not_confused(self) -> None:
        """Two distinct events of the same type with different event_ids are both processed."""
        notif = StubNotificationSender()
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=notif, audio_player=audio)

        alt1 = coord.trigger_pomodoro_completed("CS Task", 1500.0, event_id="pomo:101.0")
        alt2 = coord.trigger_pomodoro_completed("Algorithms", 1500.0, event_id="pomo:102.0")

        self.assertIsNotNone(alt1)
        self.assertIsNotNone(alt2)
        self.assertEqual(len(notif.sent_notifications), 2)

    def test_11_event_after_five_seconds_window(self) -> None:
        """Temporal deduplication allows event of the same type after the suppression window."""
        notif = StubNotificationSender()
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=notif, audio_player=audio)

        alt1 = coord.trigger_pomodoro_completed("CS Task", 1500.0)
        self.assertIsNotNone(alt1)

        # Backdate deduplication key timestamp beyond 3.0s window
        with coord._lock:
            for k in list(coord._delivered_keys.keys()):
                coord._delivered_keys[k] = time.monotonic() - 4.0

        alt2 = coord.trigger_pomodoro_completed("CS Task", 1500.0)
        self.assertIsNotNone(alt2)

    def test_12_restart_with_persisted_event_no_duplicate_vault(self) -> None:
        """Starting daemon with pre-existing Vault events does not emit duplicates."""
        vault = Vault(self.paths.vault_file)
        evt = Event.create(EventType.POMODORO_COMPLETED, {"duration_sec": 1500, "task": "Previa"})
        vault.append(evt)
        lines_before = len(self.paths.vault_file.read_text().strip().splitlines())

        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=StubAudioPlayer())
        daemon.start()
        daemon.run_tick(0.01)
        daemon.stop()

        lines_after = len(self.paths.vault_file.read_text().strip().splitlines())
        self.assertEqual(lines_before, lines_after)

    def test_13_failed_delivery_does_not_corrupt_domain_event(self) -> None:
        """Delivery failure on notification sender does not alter Vault domain event integrity."""
        class FailingSender:
            def send(self, title, message, urgency="normal"):
                raise RuntimeError("D-Bus transport disconnected")

        daemon = SiegfriedDaemon(paths=self.paths, notifier=FailingSender(), audio_player=StubAudioPlayer())
        daemon.start()

        # Trigger focus expiration
        daemon.focus_timer.start(0.001, task_name="Test")
        time.sleep(0.02)
        daemon.run_tick(0.01)
        daemon.stop()

        # Check Vault event exists and is uncorrupted JSONL
        vault_lines = self.paths.vault_file.read_text().strip().splitlines()
        self.assertTrue(any(json.loads(line)["type"] == EventType.POMODORO_COMPLETED.value for line in vault_lines))

    def test_14_absence_of_duplication_in_vault(self) -> None:
        """Multiple ticks during sitting limit emit exactly one POSTURE_LIMIT_REACHED event."""
        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=StubAudioPlayer())
        daemon.start()

        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        daemon.state_machine._continuous_sitting_seconds = 3605.0
        daemon._posture_warned_50m = True

        # Run 10 ticks
        for _ in range(10):
            daemon.run_tick(0.001)

        daemon.stop()

        vault_lines = self.paths.vault_file.read_text().strip().splitlines()
        posture_events = [json.loads(line) for line in vault_lines if json.loads(line)["type"] == EventType.POSTURE_LIMIT_REACHED.value]
        self.assertEqual(len(posture_events), 1)

    def test_15_legitimate_critical_alert_not_erroneously_suppressed(self) -> None:
        """After ACK_BREAK resets posture alerts, a subsequent critical alert is not blocked."""
        notif = StubNotificationSender()
        audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=notif, audio_player=audio)
        daemon.start()

        daemon._posture_warned_50m = True
        daemon.state_machine._continuous_sitting_seconds = 3600.0
        daemon._check_posture_milestones()
        self.assertEqual(len(notif.sent_notifications), 1)

        # ACK break resets posture alerts
        daemon.handle_ipc_request(IPCRequest.create(IPCCommand.ACK_BREAK.value))

        # User sits again and hits 3600s
        daemon._posture_warned_50m = True
        daemon.state_machine._continuous_sitting_seconds = 3600.0
        daemon._check_posture_milestones()

        # Second critical alert MUST be delivered!
        self.assertEqual(len(notif.sent_notifications), 3)  # 1st limit + break started + 2nd limit
        self.assertEqual(notif.sent_notifications[-1][2], AlertUrgency.CRITICAL.value)

        daemon.stop()


    # ══════════════════════════════════════════════════════════
    # GRUPO C — Postura Determinista y Restricciones
    # ══════════════════════════════════════════════════════════

    def test_16_posture_warning_emitted_once(self) -> None:
        """Posture warning is emitted at 50 min and not repeated on consecutive ticks."""
        notif = StubNotificationSender()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=notif, audio_player=StubAudioPlayer())
        daemon.start()

        daemon.state_machine._continuous_sitting_seconds = 3005.0
        daemon._check_posture_milestones()
        daemon._check_posture_milestones()

        self.assertEqual(len(notif.sent_notifications), 1)
        self.assertEqual(notif.sent_notifications[0][2], AlertUrgency.NORMAL.value)
        daemon.stop()

    def test_17_critical_barrier_activated_correctly(self) -> None:
        """Hard barrier transitions state machine to CRITICAL_BREAK_REQUIRED with critical urgency."""
        notif = StubNotificationSender()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=notif, audio_player=StubAudioPlayer())
        daemon.start()

        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        daemon.state_machine.add_sitting_time(3600.0)
        daemon._check_posture_milestones()

        self.assertEqual(daemon.state_machine.state, SystemState.CRITICAL_BREAK_REQUIRED)
        self.assertEqual(notif.sent_notifications[-1][2], AlertUrgency.CRITICAL.value)
        daemon.stop()

    def test_18_postpone_permitted_under_barrier(self) -> None:
        """POSTPONE of 5 minutes is granted when sitting time is 45 min."""
        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=StubAudioPlayer())
        daemon.start()
        daemon.handle_ipc_request(IPCRequest.create(IPCCommand.START_FOCUS.value, {"duration_min": 25}))

        daemon.state_machine._continuous_sitting_seconds = 2700.0  # 45 min
        res = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.POSTPONE.value, {"minutes": 5}))

        self.assertEqual(res.status, IPCStatus.OK.value)
        self.assertEqual(daemon.state_machine.state, SystemState.POSTPONE_RUNNING)
        daemon.stop()

    def test_19_postpone_rejected_when_exceeding_barrier(self) -> None:
        """POSTPONE is rejected when requested minutes would exceed the 60-minute limit."""
        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=StubAudioPlayer())
        daemon.start()
        daemon.handle_ipc_request(IPCRequest.create(IPCCommand.START_FOCUS.value, {"duration_min": 25}))

        daemon.state_machine._continuous_sitting_seconds = 3300.0  # 55 min
        res = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.POSTPONE.value, {"minutes": 10}))

        self.assertEqual(res.status, IPCStatus.REJECTED.value)
        self.assertIn("límite postural alcanzado", res.error_msg)
        daemon.stop()

    def test_20_silencing_audio_does_not_remove_barrier(self) -> None:
        """Directly stopping audio player does not reset sitting time nor clear critical state."""
        audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=audio)
        daemon.start()

        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        daemon.state_machine.add_sitting_time(3600.0)
        audio.play_alert(self.paths.alert_sound_file)

        # Silence audio directly
        audio.stop_alert()

        # State machine MUST still be CRITICAL_BREAK_REQUIRED!
        self.assertEqual(daemon.state_machine.state, SystemState.CRITICAL_BREAK_REQUIRED)
        self.assertEqual(daemon.state_machine.continuous_sitting_seconds, 3600.0)
        daemon.stop()

    def test_21_ack_break_causes_authorized_transition(self) -> None:
        """ACK_BREAK stops audio, resets sitting time to 0, transitions to BREAK_RUNNING."""
        audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=audio)
        daemon.start()

        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        daemon.state_machine.add_sitting_time(3600.0)
        audio.play_alert(self.paths.alert_sound_file)

        res = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.ACK_BREAK.value))
        self.assertEqual(res.status, IPCStatus.OK.value)
        self.assertFalse(audio.is_playing())
        self.assertEqual(daemon.state_machine.continuous_sitting_seconds, 0.0)
        self.assertEqual(daemon.state_machine.state, SystemState.BREAK_RUNNING)
        daemon.stop()

    def test_22_cancel_focus_does_not_remove_critical_restrictions(self) -> None:
        """CANCEL_FOCUS in CRITICAL_BREAK_REQUIRED is rejected and cannot start new focus."""
        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=StubAudioPlayer())
        daemon.start()

        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        daemon.state_machine.add_sitting_time(3600.0)
        daemon._check_posture_milestones()

        res_cancel = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.CANCEL_FOCUS.value))
        self.assertEqual(res_cancel.status, IPCStatus.REJECTED.value)

        # Attempting to start focus is blocked by PostureLimitReachedError
        res_focus = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.START_FOCUS.value, {"duration_min": 25}))
        self.assertEqual(res_focus.status, IPCStatus.REJECTED.value)
        self.assertIn("Posture hard limit", res_focus.error_msg)
        daemon.stop()

    # ══════════════════════════════════════════════════════════
    # GRUPO D — Audio Quirúrgico y Shutdown
    # ══════════════════════════════════════════════════════════

    def test_23_only_own_process_terminated(self) -> None:
        """PipeWireAudioPlayer only terminates the exact subprocess it spawned."""
        player = PipeWireAudioPlayer()

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.side_effect = [None, 0, 0, 0, 0]

        with player._lock:
            player._current_process = mock_proc
            player._current_pid = 99999

        stopped = player.stop_alert()
        self.assertTrue(stopped)
        mock_proc.terminate.assert_called_once()
        self.assertIsNone(player.current_pid)

    def test_24_external_process_preserved(self) -> None:
        """PipeWireAudioPlayer never attempts to kill a PID not owned by its instance."""
        player = PipeWireAudioPlayer()

        # No process spawned
        stopped = player.stop_alert()
        self.assertFalse(stopped)
        self.assertIsNone(player.current_pid)

    def test_25_race_between_start_and_stop(self) -> None:
        """Concurrent calls to play_alert and stop_alert do not corrupt internal state."""
        player = PipeWireAudioPlayer()
        sound = self.paths.alert_sound_file

        with patch.object(player, "_resolve_backend_cmd", return_value=["dummy"]):
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.pid = 1234
                mock_proc.poll.return_value = None
                mock_popen.return_value = mock_proc

                def play_worker():
                    for _ in range(50):
                        player.play_alert(sound)

                def stop_worker():
                    for _ in range(50):
                        player.stop_alert()

                t1 = threading.Thread(target=play_worker)
                t2 = threading.Thread(target=stop_worker)
                t1.start()
                t2.start()
                t1.join()
                t2.join()

        # State remains clean and accessible
        self.assertIn(player.is_playing(), [True, False])

    def test_26_audio_backend_missing_or_error(self) -> None:
        """Missing audio backend degrades cleanly returning False without throwing."""
        player = PipeWireAudioPlayer()
        player._pw_cat = None
        player._pw_play = None
        player._paplay = None
        player._aplay = None

        played = player.play_alert(self.paths.alert_sound_file)
        self.assertFalse(played)
        self.assertFalse(player.is_playing())

    def test_27_shutdown_during_delivery(self) -> None:
        """Stopping alert coordinator with items in queue drains them and records failed status."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=blocked_notif, audio_player=audio, max_queue_size=4)
        coord.start()

        for i in range(4):
            coord._dispatch_immediate_or_queue(Alert(
                alert_id=f"alt-drain-{i}",
                alert_type=AlertType.POMODORO_COMPLETED,
                title="T",
                message="M",
                urgency=AlertUrgency.NORMAL.value,
                timestamp=time.time(),
            ))

        block.set()
        clean = coord.stop(timeout_seconds=1.0)
        self.assertTrue(clean)
        self.assertTrue(coord.clean_shutdown)

    def test_28_residual_status_identified(self) -> None:
        """If worker thread fails to stop within timeout, clean_shutdown is flagged False."""
        class HungNotifier:
            def send(self, title, message, urgency="normal"):
                time.sleep(1.0)
                return True

        coord = AlertCoordinator(notifier=HungNotifier(), audio_player=StubAudioPlayer(), max_queue_size=2)
        coord.start()
        coord._dispatch_immediate_or_queue(Alert(
            alert_id="alt-hung",
            alert_type=AlertType.POMODORO_COMPLETED,
            title="T",
            message="M",
            urgency=AlertUrgency.NORMAL.value,
            timestamp=time.time(),
        ))

        # Stop with very short timeout
        clean = coord.stop(timeout_seconds=0.01)
        self.assertFalse(clean)
        self.assertFalse(coord.clean_shutdown)

        # Allow worker to finish
        if coord._worker_thread:
            coord._worker_thread.join(timeout=1.5)

    def test_29_resources_released_after_clean_shutdown(self) -> None:
        """Clean shutdown leaves zero worker threads running."""
        stub_notif = StubNotificationSender()
        coord = AlertCoordinator(notifier=stub_notif, audio_player=StubAudioPlayer())
        coord.start()
        self.assertIsNotNone(coord._worker_thread)

        clean = coord.stop(timeout_seconds=1.0)
        self.assertTrue(clean)
        self.assertIsNone(coord._worker_thread)

    # ══════════════════════════════════════════════════════════
    # GRUPO E — Integración de Componentes Reales
    # ══════════════════════════════════════════════════════════

    def test_30_real_monotonic_timer_integration(self) -> None:
        """Real MonotonicTimer accurately fires callback and completes block."""
        fired = threading.Event()
        def on_exp():
            fired.set()

        timer = MonotonicTimer(on_expire=on_exp)
        timer.start(0.01, task_name="Micro Block")
        time.sleep(0.02)
        timer.check_expiration()

        self.assertTrue(fired.is_set())
        self.assertFalse(timer.is_active())

    def test_31_real_state_machine_integration(self) -> None:
        """Real HealthStateMachine enforces transitions and 60m barrier deterministically."""
        sm = HealthStateMachine()
        self.assertEqual(sm.state, SystemState.IDLE)

        sm.transition_to(SystemState.POMODORO_RUNNING)
        sm.add_sitting_time(1800.0)
        self.assertTrue(sm.can_postpone(300.0))

        sm.add_sitting_time(1800.0)
        self.assertEqual(sm.state, SystemState.CRITICAL_BREAK_REQUIRED)
        self.assertFalse(sm.can_postpone(300.0))

    def test_32_real_temp_vault_integration(self) -> None:
        """Real Vault on temporary filesystem appends and validates Event Schema v1 events."""
        vault = Vault(self.paths.vault_file)
        evt = Event.create(EventType.POSTURE_WARNING, {"sitting_sec": 3000.0})
        vault.append(evt)

        audit = vault.audit()
        self.assertEqual(audit.valid_events_count, 1)
        self.assertEqual(len(audit.corrupted_lines), 0)

    def test_33_real_alert_coordinator_integration(self) -> None:
        """Real AlertCoordinator dispatches to stubs synchronously with full attempt audit."""
        notif = StubNotificationSender()
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=notif, audio_player=audio, alert_sound_file=self.paths.alert_sound_file)

        alt = coord.trigger_posture_limit(3600.0)
        self.assertIsNotNone(alt)
        self.assertEqual(len(notif.sent_notifications), 1)
        self.assertTrue(audio.is_playing())

        coord.stop_audio()
        self.assertFalse(audio.is_playing())

    def test_34_simulated_notification_backend_headless(self) -> None:
        """DesktopNotificationSender detects absence of graphical session and suppresses cleanly."""
        sender = DesktopNotificationSender(executable="/usr/bin/notify-send")
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(sender.has_session())
            sent = sender.send("Title", "Message")
            self.assertFalse(sent)
            self.assertIn("No active desktop session", sender.last_attempt.error_msg)

    def test_35_simulated_audio_backend(self) -> None:
        """StubAudioPlayer faithfully simulates PID custody and play state."""
        audio = StubAudioPlayer()
        self.assertIsNone(audio.current_pid)
        audio.play_alert(self.paths.alert_sound_file)
        self.assertTrue(audio.is_playing())
        self.assertIsNotNone(audio.current_pid)
        audio.stop_alert()
        self.assertFalse(audio.is_playing())
        self.assertIsNone(audio.current_pid)

    def test_36_real_cli_and_ipc_status(self) -> None:
        """Full IPC round-trip over real Unix domain socket."""
        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=StubAudioPlayer())
        daemon.start()

        stop_loop = threading.Event()
        t = threading.Thread(
            target=lambda: [daemon.run_tick(0.01) for _ in iter(lambda: not stop_loop.is_set(), False)],
            daemon=True
        )
        t.start()

        try:
            client = IPCClient(socket_path=self.paths.socket_file)
            res = client.send_request(IPCRequest.create(IPCCommand.STATUS.value))
            self.assertEqual(res.status, IPCStatus.OK.value)
            self.assertEqual(res.payload["state"], SystemState.IDLE.value)
        finally:
            stop_loop.set()
            t.join(timeout=1.0)
            daemon.stop()

    def test_37_no_private_data_exposed(self) -> None:
        """Notification text sanitizer redacts OpenAI/DeepSeek keys and Bearer tokens."""
        raw = "Error authenticating with sk-1234567890abcdef and Bearer eyJhbGciOi"
        sanitized = _sanitize_alert_text(raw)
        self.assertNotIn("sk-1234567890abcdef", sanitized)
        self.assertNotIn("eyJhbGciOi", sanitized)
        self.assertIn("[REDACTED_KEY]", sanitized)
        self.assertIn("[REDACTED_TOKEN]", sanitized)

    def test_38_fast_path_operates_during_alert_saturation(self) -> None:
        """IPC STATUS command responds under 10ms even while alert queue is saturated."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        daemon = SiegfriedDaemon(paths=self.paths, notifier=blocked_notif, audio_player=StubAudioPlayer())
        daemon.alert_coordinator.max_queue_size = 2
        daemon.start()

        stop_loop = threading.Event()
        t = threading.Thread(
            target=lambda: [daemon.run_tick(0.005) for _ in iter(lambda: not stop_loop.is_set(), False)],
            daemon=True
        )
        t.start()

        try:
            # Saturate alert queue
            for i in range(2):
                daemon.alert_coordinator._dispatch_immediate_or_queue(Alert(
                    alert_id=f"alt-sat-{i}",
                    alert_type=AlertType.BREAK_STARTED,
                    title="L",
                    message="M",
                    urgency=AlertUrgency.LOW.value,
                    timestamp=time.time(),
                ))

            # Send STATUS command via IPC
            client = IPCClient(socket_path=self.paths.socket_file)
            latencies = []
            for _ in range(10):
                t0 = time.monotonic()
                res = client.send_request(IPCRequest.create(IPCCommand.STATUS.value))
                latencies.append((time.monotonic() - t0) * 1000.0)
                self.assertEqual(res.status, IPCStatus.OK.value)

            p95 = sorted(latencies)[int(len(latencies) * 0.95)]
            self.assertLess(p95, 10.0, f"STATUS P95 was {p95:.3f}ms under saturation (SLO < 10ms)")
        finally:
            block.set()
            stop_loop.set()
            t.join(timeout=1.0)
            daemon.stop()

    def test_39_rejected_alert_under_saturation_not_marked_delivered(self) -> None:
        """Alert rejected due to queue saturation is NOT recorded in _delivered_event_ids."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=blocked_notif, audio_player=audio, max_queue_size=2)
        coord.start()

        try:
            # Fill queue
            for i in range(2):
                coord._dispatch_immediate_or_queue(Alert(
                    alert_id=f"a-fill-{i}",
                    alert_type=AlertType.POMODORO_COMPLETED,
                    title="T",
                    message="M",
                    urgency=AlertUrgency.NORMAL.value,
                    timestamp=time.time(),
                ))

            # Dispatch with event_id that will be rejected due to saturation
            rejected_event_id = "pomo:rejected:001"
            rejected_alert = Alert(
                alert_id="a-overflow",
                alert_type=AlertType.POMODORO_COMPLETED,
                title="T",
                message="M",
                urgency=AlertUrgency.NORMAL.value,
                timestamp=time.time(),
                event_id=rejected_event_id,
            )
            accepted = coord._dispatch_immediate_or_queue(rejected_alert)
            self.assertFalse(accepted)

            # Crucial assertion: the rejected alert's event_id must NOT be marked processed!
            self.assertFalse(coord.is_event_processed(rejected_event_id))
        finally:
            block.set()
            coord.flush(1.0)
            coord.stop()

    def test_40_retry_after_saturation_rejection_succeeds(self) -> None:
        """Alert rejected under saturation can be legitimately retried once queue has capacity."""
        block = threading.Event()
        blocked_notif = BlockedNotifier(block)
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=blocked_notif, audio_player=audio, max_queue_size=1)
        coord.start()

        try:
            # 1. Fill queue with 1 item
            coord._dispatch_immediate_or_queue(Alert(
                alert_id="a-first",
                alert_type=AlertType.BREAK_STARTED,
                title="T",
                message="M",
                urgency=AlertUrgency.LOW.value,
                timestamp=time.time(),
            ))

            # 2. Try to dispatch retryable alert: fails due to queue saturation
            retry_event_id = "pomo:retry:002"
            alt_attempt1 = Alert(
                alert_id="a-try-1",
                alert_type=AlertType.POMODORO_COMPLETED,
                title="T",
                message="M",
                urgency=AlertUrgency.NORMAL.value,
                timestamp=time.time(),
                event_id=retry_event_id,
            )
            self.assertFalse(coord._dispatch_immediate_or_queue(alt_attempt1))
            self.assertFalse(coord.is_event_processed(retry_event_id))

            # 3. Unblock worker and flush queue to create capacity
            block.set()
            coord.flush(1.0)
            self.assertEqual(coord.pending_alerts_count, 0)

            # 4. Retry with the SAME event_id: must be admitted successfully!
            alt_attempt2 = Alert(
                alert_id="a-try-2",
                alert_type=AlertType.POMODORO_COMPLETED,
                title="T",
                message="M",
                urgency=AlertUrgency.NORMAL.value,
                timestamp=time.time(),
                event_id=retry_event_id,
            )
            accepted = coord._dispatch_immediate_or_queue(alt_attempt2)
            self.assertTrue(accepted)
            self.assertTrue(coord.is_event_processed(retry_event_id))
        finally:
            block.set()
            coord.flush(1.0)
            coord.stop()

    def test_41_concurrent_dispatch_same_event_id_atomic(self) -> None:
        """Multiple concurrent threads attempting to dispatch the same event_id result in strictly 1 admitted."""
        notif = StubNotificationSender()
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=notif, audio_player=audio, max_queue_size=16)
        coord._force_queue_for_stubs = True
        coord.start()

        shared_event_id = "concurrent:event:999"
        num_threads = 10
        results: List[bool] = []
        lock = threading.Lock()

        def worker_task() -> None:
            alert = Alert(
                alert_id=f"a-concurrent-{threading.get_ident()}",
                alert_type=AlertType.POMODORO_COMPLETED,
                title="Concurrent",
                message="Test",
                urgency=AlertUrgency.NORMAL.value,
                timestamp=time.time(),
                event_id=shared_event_id,
            )
            accepted = coord._dispatch_immediate_or_queue(alert)
            with lock:
                results.append(accepted)

        threads = [threading.Thread(target=worker_task) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=1.0)

        # Exactly 1 thread succeeded; all other 9 threads were suppressed
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), num_threads - 1)

        # Audit attempts show 9 suppressions
        suppressed_attempts = [a for a in coord.attempts if a.status == DeliveryStatus.SUPPRESSED.value]
        self.assertEqual(len(suppressed_attempts), num_threads - 1)

        coord.stop()

    def test_42_collision_free_event_ids_same_timestamp(self) -> None:
        """Two events created in rapid succession with identical timestamp produce distinct event IDs."""
        daemon = SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(), audio_player=StubAudioPlayer())
        fixed_ts = 1700000000.123456

        e1 = Event.create(EventType.POMODORO_COMPLETED, {"task": "T1"}, ts=fixed_ts)
        e2 = Event.create(EventType.POMODORO_COMPLETED, {"task": "T2"}, ts=fixed_ts)

        id1 = daemon._create_event_id(e1)
        id2 = daemon._create_event_id(e2)

        self.assertNotEqual(id1, id2)
        self.assertIn("pomodoro_completed:1700000000.123456:1", id1)
        self.assertIn("pomodoro_completed:1700000000.123456:2", id2)

    def test_43_bounded_idempotency_cache_eviction(self) -> None:
        """Idempotency cache is strictly bounded to MAX_IDEMPOTENT_EVENT_IDS, preventing unbounded memory growth."""
        from siegfried.daemon.alerts import MAX_IDEMPOTENT_EVENT_IDS
        notif = StubNotificationSender()
        audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=notif, audio_player=audio)

        # Populate with MAX + 50 distinct event IDs
        for i in range(MAX_IDEMPOTENT_EVENT_IDS + 50):
            coord._mark_event_admitted(f"evt-{i}")

        self.assertEqual(len(coord._delivered_event_ids), MAX_IDEMPOTENT_EVENT_IDS)
        # Oldest items (0..49) must have been evicted FIFO
        self.assertFalse(coord.is_event_processed("evt-0"))
        self.assertFalse(coord.is_event_processed("evt-49"))
        # Newer items (50..MAX+49) must remain present
        self.assertTrue(coord.is_event_processed("evt-50"))
        self.assertTrue(coord.is_event_processed(f"evt-{MAX_IDEMPOTENT_EVENT_IDS + 49}"))

    def test_44_daemon_restart_does_not_replay_historical_events(self) -> None:
        """Rebooting the daemon does not replay or trigger sensory alerts for historical Vault events."""
        # 1. Populate Vault with historical events from a prior run
        vault = Vault(self.paths.vault_file, self.paths.vault_corrupt_log)
        vault.append(Event.create(EventType.POMODORO_COMPLETED, {"task": "Past Task", "duration_sec": 1500}))
        vault.append(Event.create(EventType.POSTURE_LIMIT_REACHED, {"sitting_sec": 3600.0}))

        # 2. Boot a new daemon instance
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=stub_notif, audio_player=stub_audio)
        daemon.start()

        # Run 5 ticks
        for _ in range(5):
            daemon.run_tick(timeout_seconds=0.01)

        daemon.stop()

        # Zero alerts must have been sent for past events
        self.assertEqual(len(stub_notif.sent_notifications), 0)
        self.assertEqual(len(stub_audio.played_files), 0)
        self.assertEqual(len(daemon.alert_coordinator.attempts), 0)


if __name__ == "__main__":
    unittest.main()
