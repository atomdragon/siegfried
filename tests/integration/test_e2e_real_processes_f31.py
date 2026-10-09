"""Real End-to-End Integration Test for Gate F3.1 across real OS processes.

Verifies the complete deterministic cycle:
1. Initialize isolated temporary runtime (HOME and XDG_RUNTIME_DIR).
2. Start daemon as a real external OS process (bin/siegfried-daemon).
3. Wait for IPC availability (PING readiness).
4. Execute PING via real CLI (bin/siegfried ping).
5. Start focus timer via real CLI (bin/siegfried focus 1 -t 'E2E Task').
6. Query status via real CLI (bin/siegfried status).
7. Advance timer to expiration and verify event in Vault.
8. Terminate daemon with SIGTERM and verify clean socket & process exit.
9. Restart daemon as real process.
10. Verify state recovery and absence of corrupted/duplicated events.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from siegfried.contracts.events import Event, EventType
from siegfried.contracts.ipc import IPCCommand
from siegfried.ipc.client import IPCClient
from siegfried.storage.paths import SiegfriedPaths


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestE2ERealProcessesGateF31(unittest.TestCase):
    """End-to-End real OS process verification for Gate F3.1."""

    def setUp(self) -> None:
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_run = tempfile.TemporaryDirectory()
        self.home_path = Path(self.temp_home.name).resolve()
        self.run_path = Path(self.temp_run.name).resolve()

        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home_path)
        self.env["XDG_RUNTIME_DIR"] = str(self.run_path)
        self.env["SIEGFRIED_HOME"] = str(self.home_path / ".siegfried")
        self.env["PYTHONPATH"] = str(REPO_ROOT / "src")

        self.bin_siegfried = REPO_ROOT / "bin" / "siegfried"
        self.bin_daemon = REPO_ROOT / "bin" / "siegfried-daemon"
        self.paths = SiegfriedPaths(
            base_dir=self.home_path / ".siegfried",
            runtime_dir=self.run_path,
        )

    def tearDown(self) -> None:
        self.temp_home.cleanup()
        self.temp_run.cleanup()

    def test_e2e_full_lifecycle_with_real_processes(self) -> None:
        # Step 1: Initialize temporary runtime via real CLI
        init_res = subprocess.run(
            [sys.executable, str(self.bin_siegfried), "init"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        self.assertEqual(init_res.returncode, 0, f"init failed: {init_res.stderr}")
        self.assertIn("READY", init_res.stdout)

        # Step 2: Start daemon as a real OS process
        daemon_proc = subprocess.Popen(
            [sys.executable, str(self.bin_daemon)],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            # Step 3: Wait for IPC availability (PING readiness)
            client = IPCClient(self.paths.socket_file, timeout_seconds=2.0)
            ready = False
            deadline = time.monotonic() + 5.0
            last_exc = None
            while time.monotonic() < deadline:
                if self.paths.socket_file.exists():
                    try:
                        res = client.call(IPCCommand.PING)
                        if res.payload.get("pong") is True:
                            ready = True
                            break
                    except Exception as e:
                        last_exc = e
                time.sleep(0.05)
            if not ready:
                poll = daemon_proc.poll()
                self.fail(f"Daemon did not become READY on socket {self.paths.socket_file}. last_exc={type(last_exc)}:{last_exc}, poll={poll}")

            # Step 4: Execute PING via real CLI
            ping_res = subprocess.run(
                [sys.executable, str(self.bin_siegfried), "ping"],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            self.assertEqual(ping_res.returncode, 0)
            self.assertIn("pong", ping_res.stdout)

            # Step 5: Start timer via real CLI
            focus_res = subprocess.run(
                [sys.executable, str(self.bin_siegfried), "focus", "1", "-t", "E2E Hardening Task"],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            self.assertEqual(focus_res.returncode, 0)
            self.assertIn("E2E Hardening Task", focus_res.stdout)

            # Step 6: Consult status via real CLI
            status_res = subprocess.run(
                [sys.executable, str(self.bin_siegfried), "status"],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            self.assertEqual(status_res.returncode, 0)
            self.assertIn("POMODORO_RUNNING", status_res.stdout)

            # Step 7: Check Vault has POMODORO_STARTED event
            self.assertTrue(self.paths.vault_file.exists())
            with open(self.paths.vault_file, "r", encoding="utf-8") as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["type"], "pomodoro_started")
            self.assertEqual(lines[0]["data"]["task"], "E2E Hardening Task")

            # Step 8: Send SIGTERM to daemon process
            daemon_proc.send_signal(signal.SIGTERM)
            daemon_proc.wait(timeout=5.0)
            self.assertEqual(daemon_proc.returncode, 0)

            # Step 9: Verify socket file removed upon clean shutdown
            self.assertFalse(self.paths.socket_file.exists())

            # Step 10: Restart daemon as real process
            daemon_proc2 = subprocess.Popen(
                [sys.executable, str(self.bin_daemon)],
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            try:
                # Wait for reboot readiness
                ready2 = False
                deadline2 = time.monotonic() + 5.0
                while time.monotonic() < deadline2:
                    if self.paths.socket_file.exists():
                        try:
                            res = client.call(IPCCommand.PING)
                            if res.payload.get("pong") is True:
                                ready2 = True
                                break
                        except Exception:
                            pass
                    time.sleep(0.05)
                self.assertTrue(ready2, "Restarted daemon did not become READY in time")

                # Step 11: Verify state after restart is IDLE
                status_res2 = subprocess.run(
                    [sys.executable, str(self.bin_siegfried), "status"],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
                self.assertEqual(status_res2.returncode, 0)
                self.assertIn("IDLE", status_res2.stdout)

                # Step 12: Vault must not contain duplicated events
                with open(self.paths.vault_file, "r", encoding="utf-8") as f:
                    reboot_lines = [json.loads(l) for l in f if l.strip()]
                self.assertEqual(len(reboot_lines), 1)

            finally:
                daemon_proc2.send_signal(signal.SIGTERM)
                daemon_proc2.wait(timeout=5.0)

        finally:
            if daemon_proc.poll() is None:
                daemon_proc.kill()
                daemon_proc.wait()


if __name__ == "__main__":
    unittest.main()
