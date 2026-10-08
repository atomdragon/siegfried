"""Unit tests for storage (paths, atomic json, and vault)."""

import json
import tempfile
import unittest
from pathlib import Path
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.atomic_json import atomic_write_json, read_json_locked
from siegfried.storage.vault import Vault
from siegfried.contracts.events import Event, EventType


class TestStorage(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.paths = SiegfriedPaths(base_dir=self.base_path, runtime_dir=self.base_path / "run")
        self.paths.ensure_directories()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_paths_resolution(self):
        self.assertEqual(self.paths.config_dir, self.base_path / "config")
        self.assertEqual(self.paths.data_dir, self.base_path / "data")
        self.assertEqual(self.paths.vault_file, self.base_path / "data" / "siegfried_vault.jsonl")
        self.assertEqual(self.paths.socket_file, self.base_path / "run" / "siegfried.sock")

    def test_atomic_json_write_and_read(self):
        target = self.paths.active_agenda_file
        lock = self.paths.active_agenda_lock_file
        data = {"v": 1, "critical_task": {"title": "Test Task"}, "secondary_tasks": [], "backlog": []}

        atomic_write_json(target, data, lock_file=lock)
        self.assertTrue(target.exists())
        self.assertTrue(lock.exists())

        loaded = read_json_locked(target, lock_file=lock)
        self.assertEqual(loaded["critical_task"]["title"], "Test Task")

    def test_vault_append_and_read(self):
        vault = Vault(self.paths.vault_file, self.paths.vault_corrupt_log)
        e1 = Event.create(EventType.POMODORO_STARTED, {"task": "Tarea 1"})
        e2 = Event.create(EventType.POMODORO_COMPLETED, {"task": "Tarea 1"})

        vault.append(e1)
        vault.append(e2)

        # Ensure every line in the file is valid JSON
        with open(self.paths.vault_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.assertEqual(len(lines), 2)
        for line in lines:
            parsed = json.loads(line)
            self.assertEqual(parsed["v"], 1)

        read_events = list(vault.read_events())
        self.assertEqual(len(read_events), 2)
        self.assertEqual(read_events[0].type, EventType.POMODORO_STARTED.value)
        self.assertEqual(read_events[1].type, EventType.POMODORO_COMPLETED.value)


if __name__ == "__main__":
    unittest.main()
