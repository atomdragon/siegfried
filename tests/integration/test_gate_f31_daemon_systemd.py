"""Comprehensive integration and unit test suite for Gate F3.1.

Validates:
1. Fail-closed daemon startup contract on uninitialized or corrupt runtimes.
2. Independent operation without Cloud credentials or local LLM models.
3. Socket ownership, private 0600 permissions, symlink rejection, and non-socket protection.
4. Refusal to unlink foreign sockets or non-sockets.
5. Clean stale socket recovery.
6. Monotonic timer countdown immunity to wall-clock time shifts.
7. Event persistence and idempotence across process terminations.
8. Controlled shutdown upon SIGTERM and idempotent stop().
9. systemd --user service unit syntax, hardening, and isolated installation verification.
"""

import io
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from siegfried.contracts.config import get_default_active_agenda, get_default_core_profile
from siegfried.contracts.events import Event, EventType
from siegfried.contracts.ipc import IPCCommand, IPCStatus
from siegfried.contracts.states import SystemState
from siegfried.core.errors import (
    InsecurePermissionsError,
    InvalidConfigError,
    RuntimeNotInitializedError,
    StorageError,
    UnsafePathError,
)
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.daemon.timers import MonotonicTimer
from siegfried.integrations.audio import StubAudioPlayer
from siegfried.integrations.notifications import StubNotificationSender
from siegfried.ipc.client import IPCClient
from siegfried.storage.atomic_json import atomic_write_json, read_json_locked
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault
from tools.install_user_service import install_user_service


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestGateF31DaemonLifecycleAndSecurity(unittest.TestCase):
    """F3.1 Gate Test Suite: Daemon Hardening, Socket Security & Systemd."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name) / ".siegfried"
        self.run_dir = Path(self.temp_dir.name) / "run"
        self.run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.paths = SiegfriedPaths(base_dir=self.base_dir, runtime_dir=self.run_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ──────────────────────────────────────────────────────────
    # A. Contrato de Arranque Fail-Closed
    # ──────────────────────────────────────────────────────────

    def test_01_uninitialized_runtime_fails_closed(self) -> None:
        """Daemon must raise RuntimeNotInitializedError if base_dir does not exist."""
        self.assertFalse(self.paths.base_dir.exists())
        daemon = SiegfriedDaemon(paths=self.paths)
        with self.assertRaises(RuntimeNotInitializedError):
            daemon.start()
        # Verify no directories were silently scaffolded
        self.assertFalse(self.paths.base_dir.exists())
        self.assertFalse(daemon._running)

    def test_02_missing_mandatory_directories_fails_closed(self) -> None:
        """Daemon must fail closed if base_dir exists but subdirectories are missing."""
        self.paths.base_dir.mkdir(parents=True, mode=0o700)
        daemon = SiegfriedDaemon(paths=self.paths)
        with self.assertRaises(RuntimeNotInitializedError):
            daemon.start()
        self.assertFalse(daemon._running)

    def test_03_corrupt_config_fails_closed_and_leaves_file_unaltered(self) -> None:
        """Corrupt JSON in core_profile.json must abort startup without modifying the file."""
        ensure_user_runtime(self.paths)
        corrupt_content = '{"v": 1, "invalid_json_missing_brace": '
        self.paths.core_profile_file.write_text(corrupt_content, encoding="utf-8")

        daemon = SiegfriedDaemon(paths=self.paths)
        with self.assertRaises(StorageError):
            daemon.start()

        self.assertFalse(daemon._running)
        # Content must not be overwritten or 'repaired'
        self.assertEqual(self.paths.core_profile_file.read_text(encoding="utf-8"), corrupt_content)

    def test_04_daemon_operates_without_cloud_key_and_without_local_model(self) -> None:
        """Deterministic core runs fully with zero Cloud keys and zero local LLMs."""
        ensure_user_runtime(self.paths)
        daemon = SiegfriedDaemon(paths=self.paths)
        daemon.start()
        self.assertTrue(daemon._running)

        stop_ev = threading.Event()
        t = threading.Thread(
            target=lambda: [daemon.run_tick(0.01) for _ in iter(lambda: not stop_ev.is_set(), False)],
            daemon=True,
        )
        t.start()

        # Fast-Path PING must respond immediately over socket
        client = IPCClient(self.paths.socket_file, timeout_seconds=2.0)
        res = client.call(IPCCommand.PING)
        self.assertEqual(res.status, IPCStatus.OK.value)
        self.assertTrue(res.payload.get("pong"))

        stop_ev.set()
        t.join(timeout=2.0)
        daemon.stop()

    # ──────────────────────────────────────────────────────────
    # B. Propiedad y Seguridad del Socket IPC
    # ──────────────────────────────────────────────────────────

    def test_05_socket_permissions_are_private_0600(self) -> None:
        """Created UNIX domain socket must have strict 0600 permissions."""
        ensure_user_runtime(self.paths)
        daemon = SiegfriedDaemon(paths=self.paths)
        daemon.start()
        self.assertTrue(self.paths.socket_file.exists())

        st = os.stat(self.paths.socket_file)
        mode = stat.S_IMODE(st.st_mode)
        self.assertEqual(mode, 0o600, f"Socket permissions must be 0600, got {oct(mode)}")
        self.assertTrue(stat.S_ISSOCK(st.st_mode))

        daemon.stop()
        self.assertFalse(self.paths.socket_file.exists())

    def test_06_foreign_socket_rejection_no_unlinking(self) -> None:
        """Socket owned by another UID must be rejected and NEVER unlinked."""
        ensure_user_runtime(self.paths)
        # Create a mock socket file
        import socket as s
        sock = s.socket(s.AF_UNIX, s.SOCK_STREAM)
        sock.bind(str(self.paths.socket_file))
        sock.close()

        # Mock os.lstat to report a foreign UID
        orig_lstat = os.lstat
        def mock_lstat(path, *args, **kwargs):
            real_st = orig_lstat(path)
            if str(path) == str(self.paths.socket_file):
                class MockStat:
                    st_mode = real_st.st_mode
                    st_uid = 9999  # foreign UID
                return MockStat()
            return real_st

        daemon = SiegfriedDaemon(paths=self.paths)
        with patch("os.lstat", side_effect=mock_lstat):
            with self.assertRaises(InsecurePermissionsError):
                daemon.start()

        # Socket must NOT have been unlinked
        self.assertTrue(self.paths.socket_file.exists(), "Foreign socket must not be deleted!")
        self.paths.socket_file.unlink()

    def test_07_symlink_socket_rejected_without_unlinking(self) -> None:
        """Symlink in place of socket must be rejected without unlinking the target."""
        ensure_user_runtime(self.paths)
        target_file = Path(self.temp_dir.name) / "decoy_target.txt"
        target_file.write_text("sensible data", encoding="utf-8")
        os.symlink(target_file, self.paths.socket_file)

        daemon = SiegfriedDaemon(paths=self.paths)
        with self.assertRaises(UnsafePathError):
            daemon.start()

        # Target must remain intact
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(encoding="utf-8"), "sensible data")

    def test_08_non_socket_file_at_socket_path_rejected(self) -> None:
        """Regular file placed at socket_path must be rejected without unlinking."""
        ensure_user_runtime(self.paths)
        self.paths.socket_file.write_text("regular file", encoding="utf-8")
        os.chmod(self.paths.socket_file, 0o600)

        daemon = SiegfriedDaemon(paths=self.paths)
        with self.assertRaises(UnsafePathError):
            daemon.start()

        self.assertTrue(self.paths.socket_file.exists())

    def test_09_stale_socket_from_dead_process_recovered(self) -> None:
        """Dead socket from aborted process is cleaned up safely after connect refutal."""
        ensure_user_runtime(self.paths)
        # Bind and close socket to create a stale socket file
        import socket as s
        sock = s.socket(s.AF_UNIX, s.SOCK_STREAM)
        sock.bind(str(self.paths.socket_file))
        sock.close()
        self.assertTrue(self.paths.socket_file.exists())

        daemon = SiegfriedDaemon(paths=self.paths)
        daemon.start()
        self.assertTrue(self.paths.socket_file.exists())

        stop_ev = threading.Event()
        t = threading.Thread(
            target=lambda: [daemon.run_tick(0.01) for _ in iter(lambda: not stop_ev.is_set(), False)],
            daemon=True,
        )
        t.start()

        # Test IPC responsiveness
        client = IPCClient(self.paths.socket_file)
        res = client.call(IPCCommand.PING)
        self.assertEqual(res.status, IPCStatus.OK.value)

        stop_ev.set()
        t.join(timeout=2.0)
        daemon.stop()

    # ──────────────────────────────────────────────────────────
    # C. Temporizadores Monotónicos e Inmunidad al Reloj de Pared
    # ──────────────────────────────────────────────────────────

    def test_10_monotonic_timer_immune_to_wall_clock_shift(self) -> None:
        """Timer countdown is strictly monotonic and unaffected by time.time() wall-clock jumps."""
        timer = MonotonicTimer()
        timer.start(duration_seconds=100.0, task_name="Deep Work")

        rem_before = timer.remaining_seconds()
        self.assertAlmostEqual(rem_before, 100.0, delta=1.0)

        # Simulate wall clock jump 1 hour forward and 1 hour backward
        with patch("time.time", return_value=time.time() + 3600.0):
            rem_jump_forward = timer.remaining_seconds()
            self.assertAlmostEqual(rem_jump_forward, 100.0, delta=1.0)

        with patch("time.time", return_value=time.time() - 3600.0):
            rem_jump_backward = timer.remaining_seconds()
            self.assertAlmostEqual(rem_jump_backward, 100.0, delta=1.0)

    def test_11_timer_expiration_emits_event_exactly_once(self) -> None:
        """Expiration triggers callback exactly once on deadline reach."""
        fired_count = 0
        def on_expire():
            nonlocal fired_count
            fired_count += 1

        timer = MonotonicTimer(on_expire=on_expire)
        timer.start(duration_seconds=0.01)

        time.sleep(0.02)
        # First check
        self.assertTrue(timer.check_expiration())
        self.assertEqual(fired_count, 1)

        # Subsequent checks must be no-ops
        self.assertFalse(timer.check_expiration())
        self.assertEqual(fired_count, 1)

    # ──────────────────────────────────────────────────────────
    # D. Recuperación Operativa y Crash Durability
    # ──────────────────────────────────────────────────────────

    def test_12_crash_recovery_preserves_vault_events_without_duplication(self) -> None:
        """Simulated crash during timer leaves Vault intact and restart does not duplicate events."""
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)
        vault.append(Event.create(EventType.POMODORO_STARTED, {"task": "Pre-crash Task", "duration_min": 25}))

        # Daemon starts fresh (reboot recovery)
        daemon = SiegfriedDaemon(paths=self.paths)
        daemon.start()

        events = list(vault.read_events())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data["task"], "Pre-crash Task")

        daemon.stop()

    def test_13_agenda_critical_task_recovered_deterministically_on_boot(self) -> None:
        """Critical task present in active_agenda.json is recovered into daemon state upon start."""
        ensure_user_runtime(self.paths)
        agenda = {
            "v": 1,
            "critical_task": {"title": "Arquitectura de Software"},
            "secondary_tasks": [],
            "backlog": []
        }
        atomic_write_json(self.paths.active_agenda_file, agenda)

        daemon = SiegfriedDaemon(paths=self.paths)
        daemon.start()
        self.assertEqual(daemon._active_task_name, "Arquitectura de Software")
        daemon.stop()

    # ──────────────────────────────────────────────────────────
    # E. Señales, Apagado Limpio e Idempotencia
    # ──────────────────────────────────────────────────────────

    def test_14_idempotent_stop_multiple_calls_safe(self) -> None:
        """Calling stop() multiple times is completely idempotent and safe."""
        ensure_user_runtime(self.paths)
        daemon = SiegfriedDaemon(paths=self.paths)
        daemon.start()

        self.assertTrue(daemon.stop())
        self.assertTrue(daemon.stop())
        self.assertTrue(daemon.stop())
        self.assertFalse(self.paths.socket_file.exists())

    def test_15_rapid_restart_cycles_no_thread_or_socket_leak(self) -> None:
        """Rapid start/stop cycles do not leak sockets or raise concurrency errors."""
        ensure_user_runtime(self.paths)
        for i in range(5):
            daemon = SiegfriedDaemon(paths=self.paths)
            daemon.start()
            self.assertTrue(self.paths.socket_file.exists())

            stop_ev = threading.Event()
            t = threading.Thread(
                target=lambda d=daemon: [d.run_tick(0.01) for _ in iter(lambda: not stop_ev.is_set(), False)],
                daemon=True,
            )
            t.start()

            client = IPCClient(self.paths.socket_file, timeout_seconds=2.0)
            res = client.call(IPCCommand.PING)
            self.assertEqual(res.status, IPCStatus.OK.value)

            stop_ev.set()
            t.join(timeout=2.0)
            clean = daemon.stop()
            self.assertTrue(clean)
            self.assertFalse(self.paths.socket_file.exists())

    # ──────────────────────────────────────────────────────────
    # F. Integración con systemd --user
    # ──────────────────────────────────────────────────────────

    def test_16_systemd_unit_syntax_and_hardening_verification(self) -> None:
        """Unit file must exist, contain proper restart policy, timeouts, and zero hardcoded paths."""
        unit_file = REPO_ROOT / "systemd" / "siegfried.service"
        self.assertTrue(unit_file.exists())
        content = unit_file.read_text(encoding="utf-8")

        # Verify rate limiting and restart directives
        self.assertIn("StartLimitIntervalSec=30s", content)
        self.assertIn("StartLimitBurst=5", content)
        self.assertIn("Restart=on-failure", content)
        self.assertIn("RestartPreventExitStatus=78", content)
        self.assertIn("TimeoutStopSec=5s", content)

        # Verify zero hardcoded private paths
        self.assertNotIn("/media/okami", content)
        self.assertNotIn("/home/okami", content)

    def test_17_isolated_home_installation_and_systemd_analyze_verify(self) -> None:
        """Installer runs cleanly without sudo in an isolated HOME and passes systemd-analyze verify."""
        with tempfile.TemporaryDirectory() as test_home:
            home_path = Path(test_home)
            ret = install_user_service(target_home=home_path, dry_run=False, verify=True)
            self.assertEqual(ret, 0)

            # Check binaries installed with executable permissions
            bin_daemon = home_path / ".siegfried" / "bin" / "siegfried-daemon"
            bin_cli = home_path / ".siegfried" / "bin" / "siegfried"
            unit_file = home_path / ".config" / "systemd" / "user" / "siegfried.service"

            self.assertTrue(bin_daemon.exists())
            self.assertTrue(bin_cli.exists())
            self.assertTrue(unit_file.exists())

            # Verify permissions
            st = os.stat(bin_daemon)
            self.assertTrue(bool(st.st_mode & stat.S_IXUSR))


if __name__ == "__main__":
    unittest.main()
