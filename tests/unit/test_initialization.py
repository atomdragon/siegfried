"""Comprehensive unit tests for runtime initialization (siegfried init).

Covers:
1. Initializing from scratch.
2. Repeated idempotent initialization.
3. Preservation of existing valid files and customizations.
4. Preservation of Vault events without data alteration.
5. Strict POSIX file and directory permissions (0700 / 0600).
6. Config Schema v1 validation for initial JSON documents.
7. secrets.env privacy and zero credential leakage.
8. Partially initialized runtimes.
9. Rejection of invalid or corrupted pre-existing files.
10. Insecure symlink detection and protection.
11. Concurrent initializers.
12. Permission failures and insecure mode detection.
13. CLI init integration and exit codes.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from siegfried.cli.app import run_cli
from siegfried.contracts.config import validate_active_agenda, validate_core_profile
from siegfried.contracts.events import Event, EventType
from siegfried.core.errors import StorageError
from siegfried.storage.initialization import RuntimeInitResult, ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


class TestRuntimeInitialization(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name) / "siegfried_test_home"
        self.runtime_path = Path(self.temp_dir.name) / "siegfried_test_run"
        self.paths = SiegfriedPaths(base_dir=self.base_path, runtime_dir=self.runtime_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. Scaffolding from scratch
    def test_01_init_from_scratch(self):
        self.assertFalse(self.base_path.exists())
        result = ensure_user_runtime(self.paths)

        self.assertTrue(result.created)
        self.assertEqual(result.status, "READY")
        self.assertTrue(self.base_path.exists())
        self.assertTrue(self.paths.config_dir.is_dir())
        self.assertTrue(self.paths.data_dir.is_dir())
        self.assertTrue(self.paths.assets_dir.is_dir())
        self.assertTrue(self.paths.sounds_dir.is_dir())
        self.assertTrue(self.paths.logs_dir.is_dir())

        self.assertTrue(self.paths.secrets_file.is_file())
        self.assertTrue(self.paths.core_profile_file.is_file())
        self.assertTrue(self.paths.active_agenda_file.is_file())
        self.assertTrue(self.paths.vault_file.is_file())
        self.assertTrue(self.paths.history_file.is_file())

    # 2. Repeated idempotent initialization
    def test_02_idempotent_repeated_initialization(self):
        res1 = ensure_user_runtime(self.paths)
        self.assertTrue(res1.created)

        hashes_run1 = {
            "secrets": _sha256_file(self.paths.secrets_file),
            "profile": _sha256_file(self.paths.core_profile_file),
            "agenda": _sha256_file(self.paths.active_agenda_file),
            "vault": _sha256_file(self.paths.vault_file),
            "history": _sha256_file(self.paths.history_file),
        }

        res2 = ensure_user_runtime(self.paths)
        self.assertFalse(res2.created)
        self.assertEqual(res2.status, "READY")

        res3 = ensure_user_runtime(self.paths)
        self.assertFalse(res3.created)
        self.assertEqual(res3.status, "READY")

        hashes_run3 = {
            "secrets": _sha256_file(self.paths.secrets_file),
            "profile": _sha256_file(self.paths.core_profile_file),
            "agenda": _sha256_file(self.paths.active_agenda_file),
            "vault": _sha256_file(self.paths.vault_file),
            "history": _sha256_file(self.paths.history_file),
        }

        self.assertEqual(hashes_run1, hashes_run3)

    # 3. Preservation of existing valid files and customizations
    def test_03_preserve_customized_files(self):
        ensure_user_runtime(self.paths)

        # Modify core_profile.json with valid custom fields
        custom_profile = {
            "v": 1,
            "user_title": "Alfred",
            "posture_limit_min": 45,
            "pomodoro_deep_work_min": 45,
            "pomodoro_short_break_min": 15,
            "pomodoro_agile_work_min": 20,
            "pomodoro_agile_break_min": 5,
            "active_courses": ["Sistemas Operativos"],
            "critical_subjects": ["Kernel"],
            "tech_stack": ["C", "Rust", "Python"]
        }
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            json.dump(custom_profile, f, indent=2)

        # Modify secrets.env with custom comment
        custom_secrets = "# Custom user comment\nDEEPSEEK_API_KEY=custom-key-123\n"
        with open(self.paths.secrets_file, "w", encoding="utf-8") as f:
            f.write(custom_secrets)

        # Modify .history
        with open(self.paths.history_file, "w", encoding="utf-8") as f:
            f.write("status\nfocus 50\n")

        profile_hash_before = _sha256_file(self.paths.core_profile_file)
        secrets_hash_before = _sha256_file(self.paths.secrets_file)
        history_hash_before = _sha256_file(self.paths.history_file)

        res = ensure_user_runtime(self.paths)
        self.assertFalse(res.created)
        self.assertEqual(res.status, "READY")

        self.assertEqual(_sha256_file(self.paths.core_profile_file), profile_hash_before)
        self.assertEqual(_sha256_file(self.paths.secrets_file), secrets_hash_before)
        self.assertEqual(_sha256_file(self.paths.history_file), history_hash_before)

    # 4. Preservation of Vault events without data alteration
    def test_04_vault_preservation_with_events(self):
        ensure_user_runtime(self.paths)

        vault = Vault(self.paths.vault_file)
        e1 = Event.create(EventType.POMODORO_STARTED, {"task": "Siegfried 1.1"})
        e2 = Event.create(EventType.POMODORO_COMPLETED, {"task": "Siegfried 1.1", "duration_min": 50})
        vault.append(e1)
        vault.append(e2)

        vault_hash_before = _sha256_file(self.paths.vault_file)
        self.assertGreater(os.path.getsize(self.paths.vault_file), 0)

        # Repeat initialization
        res = ensure_user_runtime(self.paths)
        self.assertFalse(res.created)
        self.assertEqual(res.status, "READY")

        vault_hash_after = _sha256_file(self.paths.vault_file)
        self.assertEqual(vault_hash_before, vault_hash_after)

        # Ensure events read back identically
        events = list(vault.read_events())
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].type, EventType.POMODORO_STARTED.value)
        self.assertEqual(events[1].type, EventType.POMODORO_COMPLETED.value)

    # 5. Strict POSIX file and directory permissions (0700 / 0600)
    def test_05_strict_permissions(self):
        ensure_user_runtime(self.paths)

        dirs_to_check = [
            self.base_path,
            self.paths.config_dir,
            self.paths.data_dir,
            self.paths.assets_dir,
            self.paths.sounds_dir,
            self.paths.logs_dir,
        ]
        for d in dirs_to_check:
            mode = stat.S_IMODE(os.stat(d).st_mode)
            self.assertEqual(mode, 0o700, f"Directory {d} mode is {oct(mode)}, expected 0700")
            self.assertEqual(mode & 0o077, 0, f"Directory {d} has group/world permissions: {oct(mode)}")

        files_to_check = [
            self.paths.secrets_file,
            self.paths.core_profile_file,
            self.paths.active_agenda_file,
            self.paths.vault_file,
            self.paths.history_file,
        ]
        for f in files_to_check:
            mode = stat.S_IMODE(os.stat(f).st_mode)
            self.assertEqual(mode, 0o600, f"File {f} mode is {oct(mode)}, expected 0600")
            self.assertEqual(mode & 0o077, 0, f"File {f} has group/world permissions: {oct(mode)}")

    # 6. Config Schema v1 validation for initial JSON documents
    def test_06_initial_json_validity(self):
        ensure_user_runtime(self.paths)

        with open(self.paths.core_profile_file, "r", encoding="utf-8") as f:
            profile_data = json.load(f)
        validate_core_profile(profile_data)
        self.assertEqual(profile_data["v"], 1)
        self.assertEqual(profile_data["user_title"], "Señor")
        self.assertEqual(profile_data["posture_limit_min"], 60)

        with open(self.paths.active_agenda_file, "r", encoding="utf-8") as f:
            agenda_data = json.load(f)
        validate_active_agenda(agenda_data)
        self.assertEqual(agenda_data["v"], 1)
        self.assertIsNone(agenda_data["critical_task"])
        self.assertEqual(agenda_data["secondary_tasks"], [])

    # 7. secrets.env privacy and zero credential leakage
    def test_07_secrets_env_privacy(self):
        ensure_user_runtime(self.paths)

        content = self.paths.secrets_file.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("# Siegfried Secrets Environment"))
        self.assertNotIn("sk-", content)
        self.assertNotIn("Bearer", content)
        self.assertNotIn("password", content.lower())
        self.assertNotIn("token=", content.lower())

    # 8. Partially initialized runtimes
    def test_08_partially_initialized_runtime(self):
        # Create only base dir and valid core_profile.json with 0700/0600 modes
        self.base_path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.base_path, 0o700)
        self.paths.config_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.paths.config_dir, 0o700)
        custom_profile = {
            "v": 1,
            "user_title": "CustomTitle",
            "posture_limit_min": 60,
            "active_courses": []
        }
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            json.dump(custom_profile, f)
        os.chmod(self.paths.core_profile_file, 0o600)

        self.assertFalse(self.paths.data_dir.exists())
        self.assertFalse(self.paths.active_agenda_file.exists())
        self.assertFalse(self.paths.vault_file.exists())

        res = ensure_user_runtime(self.paths)
        self.assertTrue(res.created)
        self.assertEqual(res.status, "READY")

        # Missing files are now created
        self.assertTrue(self.paths.data_dir.exists())
        self.assertTrue(self.paths.active_agenda_file.exists())
        self.assertTrue(self.paths.vault_file.exists())

        # Existing core_profile.json was preserved
        with open(self.paths.core_profile_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["user_title"], "CustomTitle")

    # 9. Rejection of invalid or corrupted pre-existing files
    def test_09_invalid_preexisting_files_rejected(self):
        self.base_path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.base_path, 0o700)
        self.paths.config_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.paths.config_dir, 0o700)
        # Corrupted JSON syntax
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json syntax ...")
        os.chmod(self.paths.core_profile_file, 0o600)

        with self.assertRaises(StorageError):
            ensure_user_runtime(self.paths)

        # Ensure corrupt file was NOT overwritten
        with open(self.paths.core_profile_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ invalid json syntax ...")

    def test_09b_invalid_vault_lines_rejected(self):
        self.base_path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.base_path, 0o700)
        self.paths.data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.paths.data_dir, 0o700)
        with open(self.paths.vault_file, "w", encoding="utf-8") as f:
            f.write("NON_JSON_HEADER_HEADER\n")
        os.chmod(self.paths.vault_file, 0o600)

        with self.assertRaises(StorageError):
            ensure_user_runtime(self.paths)

    # 10. Insecure symlink detection and protection
    def test_10_insecure_symlinks_rejected(self):
        outside_dir = Path(self.temp_dir.name) / "outside_evil_dir"
        outside_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(outside_dir, 0o700)

        self.base_path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.base_path, 0o700)
        # Create a symlink pointing outside base_path
        os.symlink(outside_dir, self.paths.config_dir)

        with self.assertRaises(StorageError) as ctx:
            ensure_user_runtime(self.paths)
        self.assertIn("Insecure symlink", str(ctx.exception))

    def test_10b_insecure_symlink_file_rejected(self):
        outside_file = Path(self.temp_dir.name) / "outside_secret.env"
        outside_file.write_text("evil", encoding="utf-8")
        os.chmod(outside_file, 0o600)

        self.base_path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.base_path, 0o700)
        self.paths.config_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.paths.config_dir, 0o700)
        os.symlink(outside_file, self.paths.secrets_file)

        with self.assertRaises(StorageError) as ctx:
            ensure_user_runtime(self.paths)
        self.assertIn("Insecure symlink", str(ctx.exception))

    # 11. Concurrent initializers
    def test_11_concurrent_initializers(self):
        def worker():
            paths = SiegfriedPaths(base_dir=self.base_path, runtime_dir=self.runtime_path)
            return ensure_user_runtime(paths)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker) for _ in range(20)]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), 20)
        for r in results:
            self.assertEqual(r.status, "READY")

        # Verify final filesystem structure
        with open(self.paths.core_profile_file, "r", encoding="utf-8") as f:
            profile = json.load(f)
        validate_core_profile(profile)

        with open(self.paths.active_agenda_file, "r", encoding="utf-8") as f:
            agenda = json.load(f)
        validate_active_agenda(agenda)

        self.assertEqual(stat.S_IMODE(os.stat(self.paths.secrets_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.core_profile_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.active_agenda_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.vault_file).st_mode), 0o600)

    # 12. Permission failures and insecure mode detection
    def test_12_insecure_preexisting_file_mode_rejected(self):
        self.base_path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.base_path, 0o700)
        self.paths.config_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.paths.config_dir, 0o700)
        with open(self.paths.secrets_file, "w", encoding="utf-8") as f:
            f.write("# Insecure\n")
        # Insecure mode: 0644 (world readable)
        os.chmod(self.paths.secrets_file, 0o644)

        with self.assertRaises(StorageError) as ctx:
            ensure_user_runtime(self.paths)
        self.assertIn("Insecure file permissions", str(ctx.exception))

    def test_12b_insecure_preexisting_dir_mode_rejected(self):
        self.base_path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.base_path, 0o700)
        # Insecure mode: 0755 (world readable/executable)
        self.paths.config_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
        os.chmod(self.paths.config_dir, 0o755)

        with self.assertRaises(StorageError) as ctx:
            ensure_user_runtime(self.paths)
        self.assertIn("Insecure directory permissions", str(ctx.exception))

    # 13. CLI init integration and exit codes
    def test_13_cli_init_integration(self):
        # Clean initialization
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            code = run_cli(["init"], paths=self.paths)
        self.assertEqual(code, 0)
        out = stdout_buf.getvalue()
        self.assertIn("[Siegfried] Runtime inicializado correctamente.", out)
        self.assertIn("Estado: READY", out)

        # Repeated initialization via CLI
        stdout_buf2 = io.StringIO()
        stderr_buf2 = io.StringIO()
        with patch("sys.stdout", stdout_buf2), patch("sys.stderr", stderr_buf2):
            code2 = run_cli(["init"], paths=self.paths)
        self.assertEqual(code2, 0)
        out2 = stdout_buf2.getvalue()
        self.assertIn("[Siegfried] Runtime existente verificado.", out2)
        self.assertIn("Estado: READY", out2)

        # Error handling via CLI (e.g. corrupted file)
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        stdout_buf3 = io.StringIO()
        stderr_buf3 = io.StringIO()
        with patch("sys.stdout", stdout_buf3), patch("sys.stderr", stderr_buf3):
            code3 = run_cli(["init"], paths=self.paths)
        self.assertEqual(code3, 1)
        err3 = stderr_buf3.getvalue()
        self.assertIn("[ERROR]", err3)


if __name__ == "__main__":
    unittest.main()
