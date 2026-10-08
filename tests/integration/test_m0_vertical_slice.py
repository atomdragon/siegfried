"""Integration test for Vertical Slice M0:
CLI -> Unix Socket -> Daemon -> START_FOCUS -> state machine -> timer -> FOCUS_COMPLETED -> vault -> notifier
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from siegfried.contracts.ipc import IPCCommand, IPCStatus
from siegfried.contracts.states import SystemState
from siegfried.contracts.events import EventType
from siegfried.storage.paths import SiegfriedPaths
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.ipc.client import IPCClient
from siegfried.integrations.notifications import StubNotificationSender
from siegfried.integrations.audio import StubAudioPlayer


class TestVerticalSliceM0(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base_path = Path(self.temp_dir.name)
        self.paths = SiegfriedPaths(base_dir=base_path, runtime_dir=base_path / "run")
        self.paths.ensure_directories()

        self.stub_notifier = StubNotificationSender()
        self.stub_audio = StubAudioPlayer()

        self.daemon = SiegfriedDaemon(
            paths=self.paths,
            notifier=self.stub_notifier,
            audio_player=self.stub_audio
        )
        self.daemon.start()

        # Run daemon polling in background thread
        self._stop_daemon_thread = False
        self.daemon_thread = threading.Thread(target=self._daemon_loop, daemon=True)
        self.daemon_thread.start()

        self.client = IPCClient(self.paths.socket_file, timeout_seconds=2.0)

    def tearDown(self):
        self._stop_daemon_thread = True
        self.daemon.stop()
        self.temp_dir.cleanup()

    def _daemon_loop(self):
        while not self._stop_daemon_thread:
            self.daemon.run_tick(timeout_seconds=0.01)
            time.sleep(0.005)

    def test_full_m0_deterministic_slice(self):
        # 1. Ping daemon
        ping_res = self.client.call(IPCCommand.PING)
        self.assertEqual(ping_res.status, IPCStatus.OK.value)
        self.assertEqual(ping_res.payload.get("pong"), True)
        self.assertEqual(ping_res.payload.get("state"), SystemState.IDLE.value)

        # 2. START_FOCUS via IPC (small duration: 0.001 min ~ 0.06s for fast test)
        focus_res = self.client.call(IPCCommand.START_FOCUS, {
            "duration_min": 0.001,
            "task": "Test Vertical Slice"
        })
        self.assertEqual(focus_res.status, IPCStatus.OK.value)
        self.assertEqual(focus_res.payload.get("task"), "Test Vertical Slice")
        self.assertEqual(self.daemon.state_machine.state, SystemState.POMODORO_RUNNING)

        # 3. Status query while running
        status_res = self.client.call(IPCCommand.STATUS)
        self.assertEqual(status_res.status, IPCStatus.OK.value)
        self.assertEqual(status_res.payload.get("state"), SystemState.POMODORO_RUNNING)
        self.assertTrue(status_res.payload.get("timer_active"))

        # 4. Wait for monotonic timer to expire (~0.15s)
        time.sleep(0.20)

        # 5. Verify timer expired and triggered FOCUS_COMPLETED -> BREAK_RUNNING
        self.assertEqual(self.daemon.state_machine.state, SystemState.BREAK_RUNNING)

        # 6. Verify notification was sent
        self.assertGreater(len(self.stub_notifier.sent_notifications), 0)
        notif = self.stub_notifier.sent_notifications[0]
        self.assertIn("Siegfried", notif[0])
        self.assertIn("Test Vertical Slice", notif[1])

        # 7. Verify events in Vault (Event Schema v1)
        events = list(self.daemon.vault.read_events())
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].type, EventType.POMODORO_STARTED.value)
        self.assertEqual(events[1].type, EventType.POMODORO_COMPLETED.value)
        self.assertEqual(events[0].v, 1)
        self.assertEqual(events[1].v, 1)

        # 8. ACK_BREAK via IPC
        ack_res = self.client.call(IPCCommand.ACK_BREAK)
        self.assertEqual(ack_res.status, IPCStatus.OK.value)

        # Verify break event registered
        events_after = list(self.daemon.vault.read_events())
        self.assertEqual(len(events_after), 3)
        self.assertEqual(events_after[2].type, EventType.BREAK_STARTED.value)


if __name__ == "__main__":
    unittest.main()
