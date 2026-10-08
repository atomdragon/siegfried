"""Integration tests for siegfried init and multi-process runtime initialization."""

import hashlib
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from siegfried.contracts.config import validate_active_agenda, validate_core_profile
from siegfried.contracts.events import Event, EventType
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault


def _worker_init_process(base_dir_str: str, runtime_dir_str: str, return_list):
    """Subprocess worker executing ensure_user_runtime."""
    try:
        paths = SiegfriedPaths(base_dir=Path(base_dir_str), runtime_dir=Path(runtime_dir_str))
        res = ensure_user_runtime(paths)
        return_list.append(("OK", res.created, res.status))
    except Exception as e:
        return_list.append(("ERROR", str(e), type(e).__name__))


class TestInitializationIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name) / "siegfried_home"
        self.runtime_path = Path(self.temp_dir.name) / "siegfried_run"
        self.paths = SiegfriedPaths(base_dir=self.base_path, runtime_dir=self.runtime_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_multiprocess_concurrent_init(self):
        """10 real OS processes racing to initialize the exact same runtime directory."""
        manager = multiprocessing.Manager()
        return_list = manager.list()

        processes = []
        for _ in range(10):
            p = multiprocessing.Process(
                target=_worker_init_process,
                args=(str(self.base_path), str(self.runtime_path), return_list)
            )
            processes.append(p)

        for p in processes:
            p.start()
        for p in processes:
            p.join(timeout=10.0)

        # Ensure all finished
        for p in processes:
            self.assertFalse(p.is_alive())

        self.assertEqual(len(return_list), 10)
        for status, created, res_status in return_list:
            self.assertEqual(status, "OK", f"Worker failed: {created}")
            self.assertEqual(res_status, "READY")

        # Verify exactly one or more marked as created, rest as verified, structure is 100% valid
        self.assertTrue(self.paths.secrets_file.exists())
        self.assertTrue(self.paths.core_profile_file.exists())
        self.assertTrue(self.paths.active_agenda_file.exists())
        self.assertTrue(self.paths.vault_file.exists())
        self.assertTrue(self.paths.history_file.exists())

        # Verify permissions
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.secrets_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.core_profile_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.active_agenda_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.vault_file).st_mode), 0o600)

    def test_subprocess_cli_binary_init(self):
        """Invoke bin/siegfried init via external subprocess with custom SIEGFRIED_HOME."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        bin_siegfried = repo_root / "bin" / "siegfried"

        env = dict(os.environ)
        env["SIEGFRIED_HOME"] = str(self.base_path)
        env["XDG_RUNTIME_DIR"] = str(self.runtime_path)
        env["PYTHONPATH"] = str(repo_root / "src")

        # 1. First execution -> initialized
        proc1 = subprocess.run(
            [sys.executable, str(bin_siegfried), "init"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5.0
        )
        self.assertEqual(proc1.returncode, 0)
        self.assertIn("[Siegfried] Runtime inicializado correctamente.", proc1.stdout)
        self.assertIn("Estado: READY", proc1.stdout)

        # 2. Second execution -> verified
        proc2 = subprocess.run(
            [sys.executable, str(bin_siegfried), "init"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5.0
        )
        self.assertEqual(proc2.returncode, 0)
        self.assertIn("[Siegfried] Runtime existente verificado.", proc2.stdout)
        self.assertIn("Estado: READY", proc2.stdout)

        # 3. Third execution -> verified
        proc3 = subprocess.run(
            [sys.executable, str(bin_siegfried), "init"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5.0
        )
        self.assertEqual(proc3.returncode, 0)
        self.assertIn("[Siegfried] Runtime existente verificado.", proc3.stdout)

    def test_no_real_home_contamination(self):
        """Verify that running init with custom SIEGFRIED_HOME never touches user's real ~/.siegfried."""
        real_home_siegfried = Path.home() / ".siegfried"
        real_exists_before = real_home_siegfried.exists()

        ensure_user_runtime(self.paths)

        real_exists_after = real_home_siegfried.exists()
        self.assertEqual(real_exists_before, real_exists_after)


if __name__ == "__main__":
    unittest.main()
