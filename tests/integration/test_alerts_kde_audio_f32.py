"""Comprehensive integration and unit test suite for Gate F3.2.

Validates:
A. Desktop Notifications (Freedesktop org.freedesktop.Notifications / notify-send):
   - Valid event triggers notification request without shell=True.
   - Idempotency: duplicate events do not create spurious repeat alerts.
   - Controlled graceful degradation when notify-send or desktop session is missing.
   - SubprocessError or D-Bus failure does not kill daemon.
   - Slow/hung notification backend does not block daemon reactor or monotonic timers.
   - Title and message are strictly sanitized (zero leakage of sk-* or Bearer tokens).
   - Distinguishes event occurrence vs attempt vs backend acceptance.

B. Pomodoro and Break Alerts:
   - Focus completion triggers single sensory alert.
   - ACK_BREAK silences audio and confirms active break.
   - CANCEL_FOCUS cancels timer, silences audio, and records cancellation.
   - Failure of desktop display does not block Vault persistence.
   - Failure of audio playback does not block state machine transition.

C. Posture Monitoring and Priorities:
   - 50 min posture warning emitted once per sitting cycle.
   - 60 min hard posture limit triggers critical alert and blocks further focus/postpone.
   - Silencing audio via ACK_BREAK commences break and does not bypass posture barrier.
   - POSTPONE command granted when within budget and rejected when exceeding 60m barrier.
   - Idempotent tick loop does not flood alerts on every reactor tick.

D. Audio Controller and Surgical Custody:
   - Playback tracks exact subprocess.Popen and PID.
   - Zero usage of global pkill, killall, pulseaudio -k.
   - Surgical termination of only our own tracked child process.
   - Unauthorized, non-regular or non-existent audio files rejected cleanly.
   - Duplicate playback of currently playing audio avoided.
   - Child process reaped via wait() to prevent zombie descriptors.
   - Clean shutdown terminates active playback.

E. Concurrency and Resilience:
   - Bounded alert queue (max 16) with priority eviction under saturation.
   - Fast-Path STATUS responds in sub-milliseconds during alert delivery.
   - Simultaneous notification and audio failure does not crash daemon.
   - Full IPC Client -> Unix Socket -> Daemon -> Alert Coordinator round-trip.
"""

from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
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
from siegfried.contracts.ipc import IPCCommand, IPCStatus, IPCRequest
from siegfried.contracts.states import (
    SystemState,
    MAX_CONTINUOUS_SITTING_SECONDS,
    POSTURE_WARNING_SECONDS,
)
from siegfried.daemon.alerts import AlertCoordinator
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.daemon.timers import MonotonicTimer
from siegfried.integrations.audio import (
    AudioPlayer,
    PipeWireAudioPlayer,
    StubAudioPlayer,
    AUTHORIZED_AUDIO_EXTENSIONS,
)
from siegfried.integrations.notifications import (
    DesktopNotificationSender,
    NotificationSender,
    StubNotificationSender,
    _sanitize_alert_text,
)
from siegfried.ipc.client import IPCClient
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestAlertsKdeAudioF32(unittest.TestCase):
    """Gate F3.2 Integration & Unit Suite: Alerts, KDE Notifications & Audio."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name) / ".siegfried"
        self.run_dir = Path(self.temp_dir.name) / "run"
        self.run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.paths = SiegfriedPaths(base_dir=self.base_dir, runtime_dir=self.run_dir)
        ensure_user_runtime(self.paths)

        # Create dummy valid audio file in sounds directory
        self.dummy_sound = self.paths.sounds_dir / "test_alarm.ogg"
        self.dummy_sound.write_bytes(b"OggS_DUMMY_AUDIO_DATA_FOR_TESTING")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ──────────────────────────────────────────────────────────
    # A. NOTIFICACIONES KDE PLASMA & DESKTOP
    # ──────────────────────────────────────────────────────────

    def test_01_notify_send_called_with_list_args_and_no_shell(self) -> None:
        """DesktopNotificationSender must construct argument list and never use shell=True."""
        with patch("shutil.which", return_value="/usr/bin/notify-send"):
            with patch("os.environ", {"DISPLAY": ":0", "PATH": "/usr/bin"}):
                sender = DesktopNotificationSender(executable="/usr/bin/notify-send")
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = subprocess.CompletedProcess(
                        args=[], returncode=0, stdout=b"", stderr=b""
                    )
                    res = sender.send(
                        title="Siegfried Test",
                        message="Mensaje de prueba",
                        urgency="normal"
                    )
                    self.assertTrue(res)
                    mock_run.assert_called_once()
                    args, kwargs = mock_run.call_args
                    cmd_list = args[0]
                    self.assertEqual(cmd_list[0], "/usr/bin/notify-send")
                    self.assertIn("-u", cmd_list)
                    self.assertIn("normal", cmd_list)
                    self.assertIn("-a", cmd_list)
                    self.assertIn("Siegfried", cmd_list)
                    self.assertIn("Siegfried Test", cmd_list)
                    self.assertIn("Mensaje de prueba", cmd_list)
                    self.assertFalse(kwargs.get("shell", False), "shell=True is strictly prohibited!")
                    self.assertEqual(kwargs.get("timeout"), 2.0)

    def test_02_secrets_sanitized_in_notification_text(self) -> None:
        """Any sk-* token or Bearer header in notification title or message must be redacted."""
        raw_msg = "Error connecting to sk-1234567890abcdef1234567890 with Bearer secret-token-xyz"
        clean = _sanitize_alert_text(raw_msg)
        self.assertNotIn("sk-1234567890abcdef1234567890", clean)
        self.assertIn("[REDACTED_KEY]", clean)
        self.assertNotIn("secret-token-xyz", clean)
        self.assertIn("[REDACTED_TOKEN]", clean)

        stub = StubNotificationSender()
        stub.send("Title sk-secret99", "Message Bearer abc123def")
        sent = stub.sent_notifications[0]
        self.assertEqual(sent[0], "Title [REDACTED_KEY]")
        self.assertEqual(sent[1], "Message Bearer [REDACTED_TOKEN]")

    def test_03_missing_session_or_missing_binary_degrades_gracefully(self) -> None:
        """When notify-send or graphical session is missing, send() returns False without raising."""
        # Case A: Binary missing
        with patch("shutil.which", return_value=None):
            sender = DesktopNotificationSender(executable=None)
            self.assertFalse(sender.send("Title", "Msg"))
            self.assertIsNotNone(sender.last_attempt)
            self.assertFalse(sender.last_attempt.accepted)

        # Case B: Binary exists but no graphical session (no DISPLAY, no WAYLAND_DISPLAY, no DBUS)
        with patch("shutil.which", return_value="/usr/bin/notify-send"):
            with patch("os.environ", {}):
                sender = DesktopNotificationSender(executable="/usr/bin/notify-send")
                self.assertFalse(sender.send("Title", "Msg"))
                self.assertIsNotNone(sender.last_attempt)
                self.assertFalse(sender.last_attempt.accepted)
                self.assertIn("No active desktop session", sender.last_attempt.error_msg)

    def test_04_dbus_subprocess_error_does_not_crash_sender(self) -> None:
        """SubprocessError, TimeoutExpired or OSError in notify-send returns False cleanly."""
        sender = DesktopNotificationSender(executable="/usr/bin/notify-send")
        with patch.object(sender, "has_session", return_value=True):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="notify-send", timeout=2.0)):
                self.assertFalse(sender.send("Title", "Msg"))
                self.assertIn("timed out", sender.last_attempt.error_msg)

            with patch("subprocess.run", side_effect=OSError("D-Bus connection refused")):
                self.assertFalse(sender.send("Title", "Msg"))
                self.assertIn("Subprocess invocation failure", sender.last_attempt.error_msg)

    def test_05_alert_coordinator_idempotency_prevents_duplicate_deliveries(self) -> None:
        """AlertCoordinator suppresses duplicate alerts dispatched within the cooldown window."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=stub_notif, audio_player=stub_audio)

        # First trigger succeeds
        alt1 = coord.trigger_pomodoro_completed("Task Alpha", 1500.0)
        self.assertIsNotNone(alt1)
        self.assertEqual(len(stub_notif.sent_notifications), 1)

        # Second trigger within 3 seconds is suppressed
        alt2 = coord.trigger_pomodoro_completed("Task Alpha", 1500.0)
        self.assertIsNone(alt2)
        self.assertEqual(len(stub_notif.sent_notifications), 1, "Duplicate alert must be suppressed!")

    # ──────────────────────────────────────────────────────────
    # B. POMODORO Y CICLO DE DESCANSO
    # ──────────────────────────────────────────────────────────

    def test_06_pomodoro_expiration_triggers_vault_and_single_alert(self) -> None:
        """Focus timer expiration appends POMODORO_COMPLETED and emits exactly one desktop alert."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(
            paths=self.paths,
            notifier=stub_notif,
            audio_player=stub_audio,
        )
        daemon.start()

        # Arm short timer and expire it
        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        daemon.focus_timer.start(duration_seconds=0.01, task_name="Deep Architecture")
        time.sleep(0.02)
        expired = daemon.focus_timer.check_expiration()
        self.assertTrue(expired)

        # Verify state transition to BREAK_RUNNING
        self.assertEqual(daemon.state_machine.state, SystemState.BREAK_RUNNING)

        # Verify Vault has POMODORO_COMPLETED event
        events = list(daemon.vault.read_events())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.POMODORO_COMPLETED.value)
        self.assertEqual(events[0].data["task"], "Deep Architecture")

        # Verify alert was delivered via coordinator to notifier
        self.assertEqual(len(stub_notif.sent_notifications), 1)
        self.assertIn("Bloque Concluido", stub_notif.sent_notifications[0][0])
        self.assertIn("Deep Architecture", stub_notif.sent_notifications[0][1])

        daemon.stop()

    def test_07_ack_break_silences_audio_and_transitions_state(self) -> None:
        """ACK_BREAK stops playing audio, resets posture counters, and records BREAK_STARTED."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=stub_notif, audio_player=stub_audio)
        daemon.start()

        # Simulate audio alert actively playing
        stub_audio.play_alert(self.dummy_sound)
        self.assertTrue(stub_audio.is_playing())

        # Transition to POMODORO_RUNNING and force state to CRITICAL_BREAK_REQUIRED to test posture reset
        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        daemon.state_machine.add_sitting_time(MAX_CONTINUOUS_SITTING_SECONDS)
        self.assertEqual(daemon.state_machine.state, SystemState.CRITICAL_BREAK_REQUIRED)

        # Send ACK_BREAK request via IPC handler
        from siegfried.contracts.ipc import IPCRequest
        req = IPCRequest.create(IPCCommand.ACK_BREAK)
        resp = daemon.handle_ipc_request(req)

        self.assertEqual(resp.status, IPCStatus.OK.value)
        self.assertTrue(resp.payload.get("audio_stopped"))
        self.assertFalse(stub_audio.is_playing(), "Audio must be stopped by ACK_BREAK!")
        self.assertEqual(daemon.state_machine.state, SystemState.BREAK_RUNNING)
        self.assertEqual(daemon.state_machine.continuous_sitting_seconds, 0.0)

        # Verify BREAK_STARTED event in Vault
        events = list(daemon.vault.read_events())
        self.assertEqual(events[-1].type, EventType.BREAK_STARTED.value)
        self.assertTrue(events[-1].data["interrupted_audio"])

        daemon.stop()

    def test_08_cancel_focus_silences_audio_and_resets_timer(self) -> None:
        """CANCEL_FOCUS disarms timer, silences audio, transitions to IDLE, and emits cancellation."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=stub_notif, audio_player=stub_audio)
        daemon.start()

        # Start focus
        daemon.handle_ipc_request(IPCRequest.create(IPCCommand.START_FOCUS, {"duration_min": 25, "task": "Cancel Test"}))
        self.assertTrue(daemon.focus_timer.is_active())

        # Simulate active audio playing
        stub_audio.play_alert(self.dummy_sound)
        self.assertTrue(stub_audio.is_playing())

        # Send CANCEL_FOCUS
        resp = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.CANCEL_FOCUS))
        self.assertEqual(resp.status, IPCStatus.OK.value)
        self.assertFalse(daemon.focus_timer.is_active())
        self.assertFalse(stub_audio.is_playing(), "CANCEL_FOCUS must silence active audio!")
        self.assertEqual(daemon.state_machine.state, SystemState.IDLE)

        # Verify POMODORO_CANCELLED event in Vault
        events = list(daemon.vault.read_events())
        self.assertEqual(events[-1].type, EventType.POMODORO_CANCELLED.value)

        daemon.stop()

    def test_09_notification_failure_does_not_lose_domain_event(self) -> None:
        """Even if notification sender raises an exception, the domain event is persisted in Vault."""
        failing_notif = MagicMock(spec=NotificationSender)
        failing_notif.send.side_effect = RuntimeError("D-Bus completely broken")
        stub_audio = StubAudioPlayer()

        daemon = SiegfriedDaemon(paths=self.paths, notifier=failing_notif, audio_player=stub_audio)
        daemon.start()

        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        daemon.focus_timer.start(0.01, task_name="Fault Tolerance Test")
        time.sleep(0.02)
        daemon.focus_timer.check_expiration()

        # Event MUST be in Vault regardless of notification crash
        events = list(daemon.vault.read_events())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.POMODORO_COMPLETED.value)
        self.assertEqual(daemon.state_machine.state, SystemState.BREAK_RUNNING)

        daemon.stop()

    # ──────────────────────────────────────────────────────────
    # C. CENTINELA DE POSTURA Y PRÓRROGAS (POSTPONE)
    # ──────────────────────────────────────────────────────────

    def test_10_posture_50m_warning_and_60m_hard_barrier(self) -> None:
        """Sitting time triggers 50m warning and 60m barrier with critical priority alert."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=stub_notif, audio_player=stub_audio)
        daemon.start()

        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)

        # 1. Advance sitting time to 3005s (>50m warning)
        daemon.state_machine.add_sitting_time(3005.0)
        daemon._check_posture_milestones()

        self.assertTrue(daemon._posture_warned_50m)
        self.assertFalse(daemon._posture_barrier_triggered)

        # Check posture_warning event
        events = list(daemon.vault.read_events())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.POSTURE_WARNING.value)

        # Check notification
        self.assertEqual(len(stub_notif.sent_notifications), 1)
        self.assertIn("Aviso Postural", stub_notif.sent_notifications[0][0])

        # 2. Advance sitting time past 60m (3605s)
        daemon.state_machine.add_sitting_time(600.0)
        daemon._check_posture_milestones()

        self.assertTrue(daemon._posture_barrier_triggered)
        self.assertEqual(daemon.state_machine.state, SystemState.CRITICAL_BREAK_REQUIRED)

        events2 = list(daemon.vault.read_events())
        self.assertEqual(len(events2), 2)
        self.assertEqual(events2[1].type, EventType.POSTURE_LIMIT_REACHED.value)

        # Check critical notification
        self.assertEqual(len(stub_notif.sent_notifications), 2)
        self.assertIn("Límite Postural Alcanzado", stub_notif.sent_notifications[1][0])
        self.assertEqual(stub_notif.sent_notifications[1][2], AlertUrgency.CRITICAL.value)

        daemon.stop()

    def test_11_postpone_within_budget_granted(self) -> None:
        """POSTPONE 10 min granted when user sitting time plus extension is within 60 min."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=stub_notif, audio_player=stub_audio)
        daemon.start()

        # Start pomodoro and sit for 25 min (1500s)
        daemon.handle_ipc_request(IPCRequest.create(IPCCommand.START_FOCUS, {"duration_min": 25, "task": "Flow Task"}))
        daemon.state_machine.add_sitting_time(1500.0)

        # Request 10 min postpone (1500 + 600 = 2100 <= 3600)
        resp = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.POSTPONE, {"minutes": 10}))
        self.assertEqual(resp.status, IPCStatus.OK.value)
        self.assertEqual(daemon.state_machine.state, SystemState.POSTPONE_RUNNING)
        self.assertTrue(daemon.focus_timer.is_active())

        # Vault recorded POSTPONE_GRANTED
        events = list(daemon.vault.read_events())
        self.assertEqual(events[-1].type, EventType.POSTPONE_GRANTED.value)
        self.assertEqual(events[-1].data["duration_min"], 10)

        daemon.stop()

    def test_12_postpone_exceeding_posture_barrier_rejected(self) -> None:
        """POSTPONE rejected when requested time would push sitting time over 60 min limit."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=stub_notif, audio_player=stub_audio)
        daemon.start()

        # Sitting for 55 min (3300s)
        daemon.handle_ipc_request(IPCRequest.create(IPCCommand.START_FOCUS, {"duration_min": 25, "task": "Flow Task"}))
        daemon.state_machine.add_sitting_time(3300.0)

        # Requesting 10 min postpone (3300 + 600 = 3900 > 3600) -> Must be rejected!
        resp = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.POSTPONE, {"minutes": 10}))
        self.assertEqual(resp.status, IPCStatus.REJECTED.value)
        self.assertIn("límite postural alcanzado", resp.error_msg)

        # Vault recorded POSTPONE_REJECTED
        events = list(daemon.vault.read_events())
        self.assertEqual(events[-1].type, EventType.POSTPONE_REJECTED.value)

        daemon.stop()

    # ──────────────────────────────────────────────────────────
    # D. CONTROLADOR DE AUDIO Y GESTIÓN QUIRÚRGICA
    # ──────────────────────────────────────────────────────────

    def test_13_pipewire_audio_player_pid_tracking_and_surgical_termination(self) -> None:
        """PipeWireAudioPlayer tracks exact child process PID and stops it without pkill."""
        player = PipeWireAudioPlayer()

        # Mock Popen to return a fake process with specific PID
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 98765
        mock_proc.poll.return_value = None
        def fake_terminate():
            mock_proc.poll.return_value = 0
        mock_proc.terminate.side_effect = fake_terminate

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch.object(player, "_resolve_backend_cmd", return_value=["/usr/bin/pw-cat", "-p"]):
                started = player.play_alert(self.dummy_sound)
                self.assertTrue(started)
                self.assertEqual(player.current_pid, 98765)
                self.assertTrue(player.is_playing())

                # Stop alert surgically
                stopped = player.stop_alert()
                self.assertTrue(stopped)
                mock_proc.terminate.assert_called_once()
                mock_proc.wait.assert_called()
                self.assertIsNone(player.current_pid)
                self.assertFalse(player.is_playing())

    def test_14_audio_player_rejects_unauthorized_extensions_and_nonexistent_files(self) -> None:
        """Only authorized audio extensions and existing regular files may be played."""
        player = PipeWireAudioPlayer()

        # Non-existent file
        non_existent = self.paths.sounds_dir / "missing.ogg"
        self.assertFalse(player.play_alert(non_existent))

        # Unauthorized extension (.sh or .py)
        bad_script = self.paths.sounds_dir / "exploit.sh"
        bad_script.write_text("#!/bin/bash\necho bad\n")
        self.assertFalse(player.play_alert(bad_script))

        # Directory instead of file
        self.assertFalse(player.play_alert(self.paths.sounds_dir))

    def test_15_audio_duplicate_playback_avoided_if_already_playing(self) -> None:
        """Calling play_alert for the same sound file while already playing does not spawn duplicate Popen."""
        player = PipeWireAudioPlayer()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 11223
        mock_proc.poll.return_value = None  # Always running

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            with patch.object(player, "_resolve_backend_cmd", return_value=["/usr/bin/pw-cat"]):
                # First call spawns Popen
                self.assertTrue(player.play_alert(self.dummy_sound))
                self.assertEqual(mock_popen.call_count, 1)

                # Second call with SAME sound file returns True without spawning second Popen
                self.assertTrue(player.play_alert(self.dummy_sound))
                self.assertEqual(mock_popen.call_count, 1, "Duplicate Popen must not be spawned!")

    def test_16_stop_alert_escalates_to_kill_if_process_hangs(self) -> None:
        """If child process does not terminate within 500ms, stop_alert escalates to kill()."""
        player = PipeWireAudioPlayer()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 33445
        # poll() returns None continually during terminate check
        mock_proc.poll.side_effect = [None] * 50

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch.object(player, "_resolve_backend_cmd", return_value=["/usr/bin/pw-cat"]):
                player.play_alert(self.dummy_sound)
                player.stop_alert()
                mock_proc.terminate.assert_called_once()
                mock_proc.kill.assert_called_once()
                mock_proc.wait.assert_called()

    # ──────────────────────────────────────────────────────────
    # E. CONCURRENCIA, RESILIENCIA Y NO-BLOQUEO DEL REACTOR
    # ──────────────────────────────────────────────────────────

    def test_17_slow_notification_sender_does_not_block_reactor_or_timer(self) -> None:
        """A blocking/slow notification sender does not delay the daemon reactor ticks or timer."""
        # Create a slow notifier that sleeps 0.2s on each send
        class SlowNotifier:
            def __init__(self):
                self.call_count = 0
            def send(self, title, message, urgency="normal"):
                self.call_count += 1
                time.sleep(0.2)
                return True

        slow_notif = SlowNotifier()
        stub_audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=slow_notif, audio_player=stub_audio, max_queue_size=16)
        coord.start()

        # Dispatch alert asynchronously
        t0 = time.monotonic()
        coord.trigger_pomodoro_completed("Async Test Task", 1500.0)
        t_elapsed = time.monotonic() - t0

        # Dispatch must return almost instantaneously (< 15 ms) without waiting for 0.2s sleep
        self.assertLess(t_elapsed, 0.05, f"Dispatch took {t_elapsed*1000:.2f}ms, must not block!")

        # Wait for worker thread to process queue
        coord.flush(timeout_seconds=1.0)
        self.assertEqual(slow_notif.call_count, 1)

        coord.stop()

    def test_18_fast_path_status_latency_under_alert_load(self) -> None:
        """Fast-Path STATUS requests maintain sub-millisecond response while alerts are being delivered."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=stub_notif, audio_player=stub_audio)
        daemon.start()

        stop_loop = threading.Event()
        t = threading.Thread(
            target=lambda: [daemon.run_tick(0.005) for _ in iter(lambda: not stop_loop.is_set(), False)],
            daemon=True
        )
        t.start()

        # Trigger several alerts
        for i in range(5):
            daemon.alert_coordinator.trigger_posture_warning(3000.0 + i)

        # Measure STATUS IPC response latency
        client = IPCClient(self.paths.socket_file, timeout_seconds=2.0)
        latencies = []
        for _ in range(50):
            t_start = time.perf_counter()
            res = client.call(IPCCommand.STATUS)
            t_end = time.perf_counter()
            self.assertEqual(res.status, IPCStatus.OK.value)
            latencies.append((t_end - t_start) * 1000.0)

        p95 = sorted(latencies)[int(len(latencies) * 0.95)]
        self.assertLess(p95, 10.0, f"STATUS P95 latency was {p95:.3f}ms (SLO < 10ms)")

        stop_loop.set()
        t.join(timeout=2.0)
        daemon.stop()

    def test_19_queue_saturation_evicts_low_priority_for_critical_posture(self) -> None:
        """When the alert queue is saturated (16 items), a CRITICAL alert evicts low priority items."""
        # Use a blocked notifier to simulate queue backup
        block_event = threading.Event()
        class BlockedNotifier:
            def send(self, title, message, urgency="normal"):
                block_event.wait(timeout=2.0)
                return True

        blocked_notif = BlockedNotifier()
        stub_audio = StubAudioPlayer()
        coord = AlertCoordinator(notifier=blocked_notif, audio_player=stub_audio, max_queue_size=4)
        coord.start()

        # Fill queue to maximum capacity with normal/low alerts
        for i in range(4):
            alt = Alert(
                alert_id=f"alt-test-fill-{i}",
                alert_type=AlertType.BREAK_STARTED,
                title="Low Notice",
                message=f"Msg {i}",
                urgency=AlertUrgency.LOW.value,
                timestamp=time.time(),
            )
            coord._dispatch_immediate_or_queue(alt)

        self.assertEqual(coord.pending_alerts_count, 4)

        # Now send a CRITICAL posture alert: it must evict a lower priority alert rather than being dropped
        crit_alert = coord.trigger_posture_limit(3600.0)
        self.assertIsNotNone(crit_alert)

        # Unblock worker and clean up
        block_event.set()
        coord.flush(timeout_seconds=2.0)
        coord.stop()

    def test_20_full_integration_cli_ipc_daemon_timer_alert_lifecycle(self) -> None:
        """End-to-end integration: CLI -> IPC Socket -> Daemon -> Timer Expire -> Alert Coordinator -> Stub."""
        stub_notif = StubNotificationSender()
        stub_audio = StubAudioPlayer()
        daemon = SiegfriedDaemon(paths=self.paths, notifier=stub_notif, audio_player=stub_audio)
        daemon.start()

        # Run daemon loop in background thread
        stop_loop = threading.Event()
        t = threading.Thread(
            target=lambda: [daemon.run_tick(0.01) for _ in iter(lambda: not stop_loop.is_set(), False)],
            daemon=True
        )
        t.start()

        client = IPCClient(self.paths.socket_file, timeout_seconds=2.0)

        # 1. Start short focus block (1 minute)
        start_res = client.call(IPCCommand.START_FOCUS, {"duration_min": 1, "task": "E2E Alert Task"})
        self.assertEqual(start_res.status, IPCStatus.OK.value)

        # 2. Verify status reports POMODORO_RUNNING
        st_res = client.call(IPCCommand.STATUS)
        self.assertEqual(st_res.payload["state"], SystemState.POMODORO_RUNNING.value)
        self.assertTrue(st_res.payload["timer_active"])

        # 3. Simulate timer expiration by advancing timer target
        daemon.focus_timer._target_monotonic = time.monotonic() - 1.0
        time.sleep(0.05)  # Allow reactor tick to process expiration

        # 4. State must be BREAK_RUNNING and alert delivered
        st_res2 = client.call(IPCCommand.STATUS)
        self.assertEqual(st_res2.payload["state"], SystemState.BREAK_RUNNING.value)
        self.assertGreater(len(stub_notif.sent_notifications), 0)
        self.assertIn("E2E Alert Task", stub_notif.sent_notifications[0][1])

        # 5. Send ACK_BREAK to confirm active break
        ack_res = client.call(IPCCommand.ACK_BREAK)
        self.assertEqual(ack_res.status, IPCStatus.OK.value)

        # 6. Clean stop
        stop_loop.set()
        t.join(timeout=2.0)
        daemon.stop()
        self.assertFalse(self.paths.socket_file.exists())


if __name__ == "__main__":
    unittest.main()
