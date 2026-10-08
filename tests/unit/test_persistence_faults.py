"""Unit tests for persistence, durability, fault tolerance, and recovery (Gate F1.3).

Validates:
1. Vault audit non-destructively distinguishes clean files, corrupt lines, and trailing truncated lines.
2. Vault append blocks when trailing lines are unclosed/truncated, preventing compounded corruption.
3. Vault read_events quarantines corrupted lines to vault.corrupt.log without mutating the vault.
4. Vault strict read mode raises StorageError on corrupted entries.
5. Vault non-UTF8 handling and encoding resilience.
6. Vault concurrent locking and timeout behavior under contention.
7. Atomic JSON durability, fsync error cleanup, and original file preservation during write failures.
8. Lock contention and timeout for read_json_locked.
9. Non-destructive detection of orphaned temporary files in config/ and data/.
10. Multi-process concurrent atomic writes with independent lockfiles.
"""

import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from siegfried.contracts.events import Event, EventType
from siegfried.core.errors import StorageError
from siegfried.storage.atomic_json import atomic_write_json, read_json_locked
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.validation import validate_user_runtime, RuntimeStatus
from siegfried.storage.vault import Vault, VaultAuditResult, audit_vault


def _worker_append_vault(vault_path_str: str, count: int, worker_id: int) -> None:
    """Top-level worker for multiprocessing vault appends."""
    v = Vault(Path(vault_path_str))
    for i in range(count):
        ev = Event.create(EventType.WINDOW_FOCUS_SAMPLED, {"worker": worker_id, "seq": i})
        v.append(ev)


def _worker_atomic_increment(target_str: str, iterations: int) -> None:
    """Top-level worker for multiprocessing atomic JSON increment under lock."""
    target = Path(target_str)
    for _ in range(iterations):
        lock_file = target.with_name(f"{target.stem}.lock")
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["counter"] += 1
            
            tmp_file = target.with_name(f"{target.name}.tmp.{os.getpid()}_{time.monotonic_ns()}")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, target)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


class TestPersistenceFaults(unittest.TestCase):
    """Exhaustive fault injection and persistence durability test suite."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name) / ".siegfried"
        self.paths = SiegfriedPaths(self.base_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # 1. Trailing truncated line detection in audit
    def test_01_trailing_truncated_line_detected_in_audit(self) -> None:
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        # Write 2 valid events
        ev1 = Event.create(EventType.POMODORO_STARTED, {"duration_min": 25, "task": "T1"})
        ev2 = Event.create(EventType.POMODORO_COMPLETED, {"duration_min": 25})
        vault.append(ev1)
        vault.append(ev2)

        # Inject a trailing truncated line (interrupted mid-write, no newline)
        with open(self.paths.vault_file, "a", encoding="utf-8") as f:
            f.write('{"v": 1, "ts": 123456.0, "type": "pomodoro_started", "data": {"dur')

        audit_res = vault.audit()
        self.assertFalse(audit_res.is_clean)
        self.assertEqual(audit_res.valid_events_count, 2)
        self.assertTrue(audit_res.has_trailing_truncated_line)
        self.assertEqual(len(audit_res.corrupted_lines), 1)
        self.assertEqual(audit_res.corrupted_lines[0].line_number, 3)
        self.assertTrue(audit_res.corrupted_lines[0].is_trailing_truncated)

    # 2. Trailing truncated line blocks vault append
    def test_02_trailing_truncated_line_blocks_vault_append(self) -> None:
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        ev1 = Event.create(EventType.POMODORO_STARTED, {"duration_min": 25})
        vault.append(ev1)

        # Truncate at EOF without newline
        with open(self.paths.vault_file, "a", encoding="utf-8") as f:
            f.write('{"v": 1, "ts": 999.0, "type": "pomodoro_started"')

        # Attempting to append must raise StorageError rather than concatenate onto the broken line
        ev_new = Event.create(EventType.BREAK_STARTED, {"duration_min": 5})
        with self.assertRaises(StorageError) as ctx:
            vault.append(ev_new)
        self.assertIn("truncated trailing line", str(ctx.exception))

    # 3. Middle corrupted line audit isolation
    def test_03_middle_corrupted_line_audit_isolation(self) -> None:
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        ev1 = Event.create(EventType.POMODORO_STARTED, {"duration_min": 25})
        ev2 = Event.create(EventType.POMODORO_COMPLETED, {"duration_min": 25})
        ev3 = Event.create(EventType.BREAK_STARTED, {"duration_min": 5})

        vault.append(ev1)
        # Inject corrupted middle line
        with open(self.paths.vault_file, "a", encoding="utf-8") as f:
            f.write('CORRUPTED_NON_JSON_LINE\n')
        vault.append(ev2)
        vault.append(ev3)

        audit_res = vault.audit()
        self.assertFalse(audit_res.is_clean)
        self.assertEqual(audit_res.valid_events_count, 3)
        self.assertFalse(audit_res.has_trailing_truncated_line)
        self.assertEqual(len(audit_res.corrupted_lines), 1)
        self.assertEqual(audit_res.corrupted_lines[0].line_number, 2)
        self.assertFalse(audit_res.corrupted_lines[0].is_trailing_truncated)

    # 4. Vault read_events skips and logs corrupt lines
    def test_04_vault_read_events_skips_and_logs_corrupt_lines(self) -> None:
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        ev1 = Event.create(EventType.POMODORO_STARTED, {"duration_min": 25})
        ev2 = Event.create(EventType.POMODORO_COMPLETED, {"duration_min": 25})
        vault.append(ev1)
        with open(self.paths.vault_file, "a", encoding="utf-8") as f:
            f.write('{"invalid_schema": true}\n')
        vault.append(ev2)

        events = list(vault.read_events(allow_corrupt_skip=True))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].type, EventType.POMODORO_STARTED.value)
        self.assertEqual(events[1].type, EventType.POMODORO_COMPLETED.value)

        # Check quarantine log
        self.assertTrue(vault.corrupt_log_path.exists())
        import stat
        log_mode = stat.S_IMODE(os.stat(vault.corrupt_log_path).st_mode)
        self.assertEqual(log_mode, 0o600)
        with open(vault.corrupt_log_path, "r", encoding="utf-8") as f:
            log_content = f.read()
        self.assertIn("Line 2 error", log_content)

    # 5. Vault read_events strict mode raises on corrupt line (default behavior)
    def test_05_vault_read_events_strict_mode_raises(self) -> None:
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        ev1 = Event.create(EventType.POMODORO_STARTED, {"duration_min": 25})
        vault.append(ev1)
        with open(self.paths.vault_file, "a", encoding="utf-8") as f:
            f.write('BAD_JSON\n')

        # Default mode (no arguments) is strict and must raise StorageError
        with self.assertRaises(StorageError):
            list(vault.read_events())
        # Explicit allow_corrupt_skip=False also raises
        with self.assertRaises(StorageError):
            list(vault.read_events(allow_corrupt_skip=False))

    # 6. Vault non-UTF8 bytes handled safely
    def test_06_vault_non_utf8_handled_safely(self) -> None:
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        with open(self.paths.vault_file, "wb") as f:
            f.write(b'{"v": 1, "ts": 1.0, "type": "pomodoro_started", "data": {}}\n')
            f.write(b'\xff\xfe\xfd\n')

        audit_res = vault.audit()
        self.assertFalse(audit_res.is_clean)
        self.assertTrue(any("no UTF-8" in err.error for err in audit_res.corrupted_lines))

    # 7. Vault concurrent append mutual exclusion
    def test_07_vault_concurrent_append_lock_contention(self) -> None:
        ensure_user_runtime(self.paths)
        vault_path = self.paths.vault_file

        processes = []
        for wid in range(4):
            p = multiprocessing.Process(target=_worker_append_vault, args=(str(vault_path), 25, wid))
            processes.append(p)
            p.start()

        for p in processes:
            p.join(timeout=10.0)
            self.assertEqual(p.exitcode, 0)

        vault = Vault(vault_path)
        audit_res = vault.audit()
        self.assertTrue(audit_res.is_clean)
        self.assertEqual(audit_res.valid_events_count, 100)

    # 8. Vault append lock timeout raises StorageError
    def test_08_vault_append_lock_timeout_raises_storage_error(self) -> None:
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        # Acquire exclusive lock externally
        lock_fd = os.open(self.paths.vault_file, os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        try:
            ev = Event.create(EventType.POMODORO_STARTED, {"duration_min": 25})
            with self.assertRaises(StorageError) as ctx:
                vault.append(ev, timeout_seconds=0.05)
            self.assertIn("Could not acquire exclusive lock", str(ctx.exception))
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    # 9. Atomic JSON write leaves target intact if interrupted before replace
    def test_09_atomic_write_json_leaves_target_intact_if_interrupted_before_replace(self) -> None:
        ensure_user_runtime(self.paths)
        target = self.paths.config_dir / "test_target.json"
        initial_data = {"key": "original_valid"}
        atomic_write_json(target, initial_data)

        # Mock os.replace to fail
        with patch("os.replace", side_effect=OSError("Disk write simulated failure")):
            with self.assertRaises(StorageError):
                atomic_write_json(target, {"key": "corrupted_or_failed"})

        # Verify original target remains intact and valid
        current = read_json_locked(target)
        self.assertEqual(current, initial_data)

    # 10. Atomic write JSON fsync failure cleans tmp file
    def test_10_atomic_write_json_fsync_mock_failure_cleans_tmp(self) -> None:
        ensure_user_runtime(self.paths)
        target = self.paths.config_dir / "fsync_test.json"
        with patch("os.fsync", side_effect=OSError("Fsync I/O error")):
            with self.assertRaises(StorageError):
                atomic_write_json(target, {"data": 123})

        self.assertFalse(target.exists())
        # Confirm no leftover .tmp files
        tmp_files = list(self.paths.config_dir.glob("fsync_test.json.tmp.*"))
        self.assertEqual(len(tmp_files), 0)

    # 11. read_json_locked timeout on exclusive lock
    def test_11_read_json_locked_timeout_on_exclusive_lock(self) -> None:
        ensure_user_runtime(self.paths)
        target = self.paths.config_dir / "locked_test.json"
        atomic_write_json(target, {"a": 1})

        lock_file = target.with_name(f"{target.stem}.lock")
        lock_fd = os.open(lock_file, os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        try:
            with self.assertRaises(StorageError) as ctx:
                read_json_locked(target, timeout_seconds=0.05)
            self.assertIn("Could not acquire shared lock", str(ctx.exception))
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    # 12. read_json_locked corrupted content raises StorageError
    def test_12_read_json_locked_corrupted_content_raises_storage_error(self) -> None:
        ensure_user_runtime(self.paths)
        target = self.paths.config_dir / "corrupted_target.json"
        with open(target, "w", encoding="utf-8") as f:
            f.write("{NOT_VALID_JSON")

        with self.assertRaises(StorageError):
            read_json_locked(target)

    # 13. Orphaned temporary files detected non-destructively
    def test_13_orphaned_tmp_files_detected_non_destructively(self) -> None:
        ensure_user_runtime(self.paths)
        orphaned_tmp1 = self.paths.config_dir / "core_profile.json.tmp.9999_12345"
        orphaned_tmp2 = self.paths.data_dir / "siegfried_vault.jsonl.tmp.8888_67890"

        with open(orphaned_tmp1, "w", encoding="utf-8") as f:
            f.write('{"partial": true}')
        with open(orphaned_tmp2, "w", encoding="utf-8") as f:
            f.write('partial vault')

        result = validate_user_runtime(self.paths)
        self.assertTrue(result.is_ready)
        self.assertEqual(len(result.orphaned_temp_files), 2)
        self.assertIn(orphaned_tmp1, result.orphaned_temp_files)
        self.assertIn(orphaned_tmp2, result.orphaned_temp_files)

        # Must not have been deleted
        self.assertTrue(orphaned_tmp1.exists())
        self.assertTrue(orphaned_tmp2.exists())

    # 14. Vault audit on empty and non-existent file
    def test_14_vault_audit_on_empty_and_nonexistent_file(self) -> None:
        non_existent = self.paths.data_dir / "non_existent.jsonl"
        res1 = audit_vault(non_existent)
        self.assertTrue(res1.is_clean)
        self.assertEqual(res1.valid_events_count, 0)

        empty_file = self.paths.data_dir / "empty.jsonl"
        empty_file.touch()
        res2 = audit_vault(empty_file)
        self.assertTrue(res2.is_clean)
        self.assertEqual(res2.valid_events_count, 0)

    # 15. Vault streaming audit scale test
    def test_15_vault_streaming_audit_large_scale(self) -> None:
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)

        # Write 500 events
        for i in range(500):
            ev = Event.create(EventType.WINDOW_FOCUS_SAMPLED, {"app": "Konsole", "idx": i})
            vault.append(ev)

        audit_res = vault.audit()
        self.assertTrue(audit_res.is_clean)
        self.assertEqual(audit_res.valid_events_count, 500)
        self.assertEqual(audit_res.total_lines_read, 500)

    # 16. Multi-process concurrent atomic writes with independent lockfiles
    def test_16_atomic_write_concurrent_processes(self) -> None:
        ensure_user_runtime(self.paths)
        target = self.paths.config_dir / "concurrent_agenda.json"
        atomic_write_json(target, {"counter": 0})

        processes = []
        for _ in range(4):
            p = multiprocessing.Process(target=_worker_atomic_increment, args=(str(target), 25))
            processes.append(p)
            p.start()

        for p in processes:
            p.join(timeout=10.0)
            self.assertEqual(p.exitcode, 0)

        final_data = read_json_locked(target)
        self.assertEqual(final_data["counter"], 100)


if __name__ == "__main__":
    unittest.main()
