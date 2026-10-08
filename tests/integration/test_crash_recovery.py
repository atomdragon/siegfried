"""Integration tests for crash recovery, SIGKILL fault injection, and persistence durability (Gate F1.3).

Validates:
1. SIGKILL during atomic JSON write leaves original configuration intact and valid.
2. Orphaned temporary files from crashed processes are detected non-destructively by diagnostics.
3. Vault durability and non-destructive audit behavior when writes are interrupted by SIGKILL.
4. OS kernel fcntl lock cleanup upon process termination (no deadlocks or stale locks).
"""

import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest

from siegfried.contracts.config import validate_core_profile
from siegfried.contracts.events import Event, EventType
from siegfried.core.errors import StorageError
from siegfried.storage.atomic_json import atomic_write_json, read_json_locked
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.validation import validate_user_runtime, RuntimeStatus
from siegfried.storage.vault import Vault, audit_vault


def _worker_atomic_write_signal_before_replace(
    target_str: str,
    data: dict,
    ready_event: multiprocessing.Event,
) -> None:
    """Worker writes temp file, signals parent, and sleeps before os.replace, awaiting SIGKILL."""
    target_file = Path(target_str)
    lock_file = target_file.with_name(f"{target_file.stem}.lock")
    tmp_file = target_file.with_name(f"{target_file.name}.tmp.{os.getpid()}_{time.monotonic_ns()}")

    lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)

    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp_fd = os.open(tmp_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with open(tmp_fd, "w", encoding="utf-8") as f:
        f.write(serialized)
        f.flush()
        os.fsync(f.fileno())

    # Signal parent that tmp file is written and flushed
    ready_event.set()

    # Hang here until SIGKILL arrives
    time.sleep(10.0)

    # Should not reach here
    os.replace(tmp_file, target_file)


def _worker_hold_lock_until_kill(lock_file_str: str, ready_event: multiprocessing.Event) -> None:
    """Worker acquires exclusive lock and waits to be killed."""
    lock_fd = os.open(lock_file_str, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    ready_event.set()
    time.sleep(10.0)


def _worker_interrupted_vault_write(
    vault_file_str: str,
    ready_event: multiprocessing.Event,
) -> None:
    """Worker starts raw partial write to vault and signals parent for SIGKILL."""
    vault_file = Path(vault_file_str)
    fd = os.open(vault_file, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)

    partial = '{"v": 1, "ts": 1234.56, "type": "pomodoro_started", "data": {"task": "Unfinished"'
    os.write(fd, partial.encode("utf-8"))
    os.fsync(fd)

    ready_event.set()
    time.sleep(10.0)


class TestCrashRecoveryIntegration(unittest.TestCase):
    """Integration test suite verifying crash recovery with real OS process termination."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name) / ".siegfried"
        self.paths = SiegfriedPaths(self.base_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sigkill_during_atomic_write_leaves_target_intact(self) -> None:
        """Process killed during atomic write before replace must leave target file intact."""
        ensure_user_runtime(self.paths)
        original_profile = read_json_locked(self.paths.core_profile_file)
        self.assertIn("user_title", original_profile)

        # Launch child process that writes temp file and awaits SIGKILL
        ready_event = multiprocessing.Event()
        new_data = {
            "v": 1,
            "user_title": "Crashed Process Modification",
            "posture_limit_min": 60,
            "pomodoro_deep_work_min": 50,
            "pomodoro_short_break_min": 10,
            "pomodoro_agile_work_min": 25,
            "pomodoro_agile_break_min": 5,
            "active_courses": [],
            "critical_subjects": [],
            "tech_stack": [".NET", "C#", "Python", "Linux"],
        }

        p = multiprocessing.Process(
            target=_worker_atomic_write_signal_before_replace,
            args=(str(self.paths.core_profile_file), new_data, ready_event),
        )
        p.start()

        # Wait for child to reach write point
        self.assertTrue(ready_event.wait(timeout=5.0), "Worker did not reach ready state")

        # Send SIGKILL
        os.kill(p.pid, signal.SIGKILL)
        p.join(timeout=5.0)
        self.assertEqual(p.exitcode, -signal.SIGKILL)

        # 1. Target file must remain in its original valid state
        current_profile = read_json_locked(self.paths.core_profile_file)
        self.assertEqual(current_profile, original_profile)
        self.assertEqual(current_profile["user_title"], "Señor")

        # 2. Orphaned temp file must be detected by validation non-destructively
        diag = validate_user_runtime(self.paths)
        self.assertTrue(diag.is_ready)
        self.assertEqual(len(diag.orphaned_temp_files), 1)

        # 3. Subsequent atomic write must succeed cleanly
        updated_data = {
            "v": 1,
            "user_title": "Recovered Write",
            "posture_limit_min": 60,
            "pomodoro_deep_work_min": 50,
            "pomodoro_short_break_min": 10,
            "pomodoro_agile_work_min": 25,
            "pomodoro_agile_break_min": 5,
            "active_courses": [],
            "critical_subjects": [],
            "tech_stack": [".NET", "C#", "Python", "Linux"],
        }
        atomic_write_json(self.paths.core_profile_file, updated_data)
        saved = read_json_locked(self.paths.core_profile_file)
        self.assertEqual(saved["user_title"], "Recovered Write")

    def test_stale_lockfile_recovery_after_process_death(self) -> None:
        """Process holding lockfile killed by SIGKILL must have lock released cleanly by kernel."""
        ensure_user_runtime(self.paths)
        lock_file = self.paths.config_dir / "active_agenda.lock"

        ready_event = multiprocessing.Event()
        p = multiprocessing.Process(
            target=_worker_hold_lock_until_kill,
            args=(str(lock_file), ready_event),
        )
        p.start()

        self.assertTrue(ready_event.wait(timeout=5.0))

        # Kill child holding lock
        os.kill(p.pid, signal.SIGKILL)
        p.join(timeout=5.0)
        self.assertEqual(p.exitcode, -signal.SIGKILL)

        # Parent should immediately be able to acquire lock and write without deadlock
        test_agenda = {
            "version": 1,
            "current_focus": {"task": "Post-Crash Task", "started_at": 100.0, "duration_min": 25},
            "secondary_tasks": [],
        }
        atomic_write_json(self.paths.active_agenda_file, test_agenda, timeout_seconds=1.0)
        read_back = read_json_locked(self.paths.active_agenda_file, timeout_seconds=1.0)
        self.assertEqual(read_back["current_focus"]["task"], "Post-Crash Task")

    def test_sigkill_during_vault_write_detected_non_destructively(self) -> None:
        """Vault interrupted by SIGKILL midway through a write is classified cleanly by audit."""
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        # Add initial valid events
        for i in range(5):
            vault.append(Event.create(EventType.POMODORO_STARTED, {"seq": i, "duration_min": 25}))

        # Worker writes partial event line and gets SIGKILLed
        ready_event = multiprocessing.Event()
        p = multiprocessing.Process(
            target=_worker_interrupted_vault_write,
            args=(str(self.paths.vault_file), ready_event),
        )
        p.start()

        self.assertTrue(ready_event.wait(timeout=5.0))
        os.kill(p.pid, signal.SIGKILL)
        p.join(timeout=5.0)
        self.assertEqual(p.exitcode, -signal.SIGKILL)

        # Audit should non-destructively detect the trailing truncated line
        audit_res = vault.audit()
        self.assertFalse(audit_res.is_clean)
        self.assertEqual(audit_res.valid_events_count, 5)
        self.assertTrue(audit_res.has_trailing_truncated_line)
        self.assertEqual(len(audit_res.corrupted_lines), 1)
        self.assertTrue(audit_res.corrupted_lines[0].is_trailing_truncated)

        # read_events with skip must still return all 5 valid events
        valid_events = list(vault.read_events(allow_corrupt_skip=True))
        self.assertEqual(len(valid_events), 5)
        self.assertEqual([e.data["seq"] for e in valid_events], [0, 1, 2, 3, 4])

        # Appending is blocked to protect history from concatenation
        with self.assertRaises(StorageError):
            vault.append(Event.create(EventType.BREAK_STARTED, {"duration_min": 5}))


if __name__ == "__main__":
    unittest.main()
