"""Integration tests for siegfried doctor and daemon startup validation."""

import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from siegfried.cli.app import run_cli
from siegfried.core.errors import StorageError
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths


class TestDoctorAndDaemonValidation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name) / "siegfried_test_home"
        self.runtime_path = Path(self.temp_dir.name) / "siegfried_test_run"
        self.paths = SiegfriedPaths(base_dir=self.base_path, runtime_dir=self.runtime_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. CLI siegfried doctor on valid runtime
    def test_cli_doctor_on_valid_runtime(self):
        ensure_user_runtime(self.paths)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            code = run_cli(["doctor"], paths=self.paths)

        self.assertEqual(code, 0)
        out = stdout_buf.getvalue()
        self.assertIn("[Siegfried] Diagnóstico del runtime", out)
        self.assertIn("Configuración: OK", out)
        self.assertIn("Permisos: OK", out)
        self.assertIn("Vault: OK", out)
        self.assertIn("Rutas: OK", out)
        self.assertIn("Estado: READY", out)

    # 2. CLI siegfried doctor on uninitialized runtime
    def test_cli_doctor_on_uninitialized_runtime(self):
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            code = run_cli(["doctor"], paths=self.paths)

        self.assertEqual(code, 1)
        out = stdout_buf.getvalue()
        self.assertIn("Estado: NOT_READY", out)

    # 3. CLI siegfried doctor on corrupted configuration
    def test_cli_doctor_on_corrupt_config(self):
        ensure_user_runtime(self.paths)
        with open(self.paths.active_agenda_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            code = run_cli(["doctor"], paths=self.paths)

        self.assertEqual(code, 1)
        out = stdout_buf.getvalue()
        self.assertIn("Configuración: INVALID_CONFIG", out)
        self.assertIn("Estado: NOT_READY", out)
        self.assertIn("Archivo: config/active_agenda.json", out)

    # 4. CLI siegfried doctor on insecure permissions
    def test_cli_doctor_on_insecure_permissions(self):
        ensure_user_runtime(self.paths)
        os.chmod(self.paths.config_dir, 0o755)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            code = run_cli(["doctor"], paths=self.paths)

        self.assertEqual(code, 1)
        out = stdout_buf.getvalue()
        self.assertIn("Permisos: INSECURE_PERMISSIONS", out)
        self.assertIn("Estado: NOT_READY", out)
        self.assertIn("Archivo: config", out)

    # 5. CLI doctor never exposes secrets
    def test_cli_doctor_never_exposes_secrets(self):
        ensure_user_runtime(self.paths)
        with open(self.paths.secrets_file, "w", encoding="utf-8") as f:
            f.write("DEEPSEEK_API_KEY=MY_SECRET_KEY_NEVER_PRINT_ME\n")
        # Insecure mode: 0644
        os.chmod(self.paths.secrets_file, 0o644)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            code = run_cli(["doctor"], paths=self.paths)

        self.assertEqual(code, 1)
        combined = stdout_buf.getvalue() + stderr_buf.getvalue()
        self.assertNotIn("MY_SECRET_KEY_NEVER_PRINT_ME", combined)

    # 6. Subprocess execution of bin/siegfried doctor
    def test_subprocess_binary_doctor(self):
        ensure_user_runtime(self.paths)
        repo_root = Path(__file__).resolve().parent.parent.parent
        bin_siegfried = repo_root / "bin" / "siegfried"

        env = dict(os.environ)
        env["SIEGFRIED_HOME"] = str(self.base_path)
        env["XDG_RUNTIME_DIR"] = str(self.runtime_path)
        env["PYTHONPATH"] = str(repo_root / "src")

        proc = subprocess.run(
            [sys.executable, str(bin_siegfried), "doctor"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5.0
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Estado: READY", proc.stdout)

    # 7. Daemon startup validation
    def test_daemon_starts_cleanly_on_valid_runtime(self):
        ensure_user_runtime(self.paths)
        daemon = SiegfriedDaemon(paths=self.paths)
        daemon.start()
        self.assertTrue(daemon._running)
        daemon.stop()

    def test_daemon_aborts_on_corrupt_config(self):
        ensure_user_runtime(self.paths)
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        daemon = SiegfriedDaemon(paths=self.paths)
        with self.assertRaises(StorageError):
            daemon.start()
        self.assertFalse(daemon._running)

    def test_daemon_aborts_on_insecure_permissions(self):
        ensure_user_runtime(self.paths)
        os.chmod(self.paths.secrets_file, 0o644)

        daemon = SiegfriedDaemon(paths=self.paths)
        with self.assertRaises(StorageError):
            daemon.start()
        self.assertFalse(daemon._running)

    def test_daemon_aborts_on_insecure_symlink(self):
        ensure_user_runtime(self.paths)
        self.paths.active_agenda_file.unlink()

        outside_target = Path(self.temp_dir.name) / "outside_agenda.json"
        outside_target.write_text('{"v":1}', encoding="utf-8")
        os.chmod(outside_target, 0o600)
        os.symlink(outside_target, self.paths.active_agenda_file)

        daemon = SiegfriedDaemon(paths=self.paths)
        with self.assertRaises(StorageError):
            daemon.start()
        self.assertFalse(daemon._running)


if __name__ == "__main__":
    unittest.main()
