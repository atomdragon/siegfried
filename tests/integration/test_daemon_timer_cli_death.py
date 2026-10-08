"""Integration test verifying timer completion and event persistence after CLI process termination (Gate F1.4 / Gate F).

Validates:
1. Daemon runs as an independent OS process.
2. Separate CLI process triggers START_FOCUS timer.
3. CLI process is terminated abruptly (SIGKILL).
4. Daemon survives CLI termination and timer continues counting down.
5. Daemon completes focus timer expiration and transitions to BREAK_RUNNING.
6. POMODORO_COMPLETED event is persisted to Vault exactly once.
7. Event adheres strictly to Event Schema v1.
8. Daemon terminates gracefully upon SIGTERM and cleans up resources.
"""

import multiprocessing
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest

from siegfried.contracts.events import Event, EventType, EVENT_SCHEMA_VERSION
from siegfried.contracts.ipc import IPCCommand, IPCStatus
from siegfried.contracts.states import SystemState
from siegfried.core.errors import StorageError
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.ipc.client import IPCClient
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.vault import Vault


def _daemon_process_worker(base_dir_str: str, run_dir_str: str, ready_event: multiprocessing.Event) -> None:
    """Independent process running Siegfried Daemon."""
    paths = SiegfriedPaths(base_dir=Path(base_dir_str), runtime_dir=Path(run_dir_str))
    daemon = SiegfriedDaemon(paths=paths)
    daemon.start()
    ready_event.set()
    try:
        while daemon._running:
            daemon.run_tick(timeout_seconds=0.05)
    finally:
        daemon.stop()


def _cli_process_worker(socket_path_str: str, duration_min: float, task_name: str, started_event: multiprocessing.Event) -> None:
    """Separate CLI process initiating a focus block and notifying parent."""
    client = IPCClient(Path(socket_path_str), timeout_seconds=2.0)
    res = client.call(IPCCommand.START_FOCUS, {"duration_min": duration_min, "task": task_name})
    if res.status == IPCStatus.OK.value:
        started_event.set()
    # Hang here until parent kills this process
    time.sleep(30.0)


class TestDaemonTimerCLIDeath(unittest.TestCase):
    """End-to-end integration test for timer completion across process boundaries."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name) / ".siegfried"
        self.run_dir = Path(self.temp_dir.name) / "run"
        self.paths = SiegfriedPaths(base_dir=self.base_dir, runtime_dir=self.run_dir)
        ensure_user_runtime(self.paths)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_timer_completes_and_persists_after_cli_killed(self) -> None:
        # 1. Start daemon as independent process
        daemon_ready = multiprocessing.Event()
        daemon_proc = multiprocessing.Process(
            target=_daemon_process_worker,
            args=(str(self.base_dir), str(self.run_dir), daemon_ready),
        )
        daemon_proc.start()

        try:
            self.assertTrue(daemon_ready.wait(timeout=5.0), "Daemon did not reach ready state")

            # Verify IPC connectivity
            client = IPCClient(self.paths.socket_file, timeout_seconds=2.0)
            ping_res = client.call(IPCCommand.PING)
            self.assertEqual(ping_res.status, IPCStatus.OK.value)

            # 2. Launch separate CLI process to start focus timer (0.005 min = 0.3 seconds)
            cli_started = multiprocessing.Event()
            cli_proc = multiprocessing.Process(
                target=_cli_process_worker,
                args=(str(self.paths.socket_file), 0.005, "Architecture Gate F1.4", cli_started),
            )
            cli_proc.start()

            self.assertTrue(cli_started.wait(timeout=5.0), "CLI process did not start focus timer")

            # Verify state is POMODORO_RUNNING
            status_res = client.call(IPCCommand.STATUS)
            self.assertEqual(status_res.payload["state"], SystemState.POMODORO_RUNNING.value)

            # 3. Kill the CLI process with SIGKILL
            os.kill(cli_proc.pid, signal.SIGKILL)
            cli_proc.join(timeout=2.0)
            self.assertFalse(cli_proc.is_alive())

            # 4. Wait for daemon timer to expire (timer duration is 0.3s)
            deadline = time.monotonic() + 3.0
            timer_completed = False
            while time.monotonic() < deadline:
                time.sleep(0.05)
                poll_res = client.call(IPCCommand.STATUS)
                if poll_res.payload["state"] == SystemState.BREAK_RUNNING.value:
                    timer_completed = True
                    break

            # 5. Confirm daemon completed the transition without CLI
            self.assertTrue(timer_completed, "Daemon failed to transition to BREAK_RUNNING after timer expiration")

            # 6. Verify events in Vault
            vault = Vault(self.paths.vault_file)
            events = list(vault.read_events())

            started_events = [e for e in events if e.type == EventType.POMODORO_STARTED.value]
            completed_events = [e for e in events if e.type == EventType.POMODORO_COMPLETED.value]

            self.assertEqual(len(started_events), 1)
            self.assertEqual(started_events[0].data["task"], "Architecture Gate F1.4")

            # 7. Confirm POMODORO_COMPLETED is persisted and NOT registered twice
            self.assertEqual(len(completed_events), 1, "POMODORO_COMPLETED must be recorded exactly once")
            self.assertEqual(completed_events[0].data["task"], "Architecture Gate F1.4")
            self.assertAlmostEqual(completed_events[0].data["duration_sec"], 0.3, delta=0.05)

            # Check Event Schema v1 conformity
            for ev in events:
                self.assertEqual(ev.v, EVENT_SCHEMA_VERSION)
                self.assertIsInstance(ev.ts, float)
                self.assertIsInstance(ev.data, dict)

        finally:
            # 8. Graceful stop of daemon process
            if daemon_proc.is_alive():
                os.kill(daemon_proc.pid, signal.SIGTERM)
                daemon_proc.join(timeout=3.0)
                if daemon_proc.is_alive():
                    daemon_proc.kill()
                    daemon_proc.join(timeout=1.0)
            self.assertFalse(daemon_proc.is_alive())


if __name__ == "__main__":
    unittest.main()
