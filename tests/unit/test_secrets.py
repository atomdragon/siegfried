"""Unit tests for secure secrets loading and privacy."""

import os
from pathlib import Path
import stat
import tempfile
import unittest

from siegfried.core.errors import (
    InsecurePermissionsError,
    InvalidConfigError,
    UnsafePathError,
)
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.secrets import get_secret, load_secrets


class TestSecretsLoader(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.paths = SiegfriedPaths(base_dir=self.base_dir, runtime_dir=self.base_dir / "run")
        self.paths.ensure_directories()
        # Initialize secrets.env with 0600
        with open(self.paths.secrets_file, "w", encoding="utf-8") as f:
            f.write("# Secrets file\nDEEPSEEK_API_KEY=sk-test-key-12345\nSIEGFRIED_LLM_API_KEY=\"sk-custom-quote\"\n")
        os.chmod(self.paths.secrets_file, 0o600)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_secrets_valid_file(self):
        secrets = load_secrets(secrets_file=self.paths.secrets_file, base_dir=self.base_dir)
        self.assertEqual(secrets.get("DEEPSEEK_API_KEY"), "sk-test-key-12345")
        self.assertEqual(secrets.get("SIEGFRIED_LLM_API_KEY"), "sk-custom-quote")

    def test_get_secret_resolution(self):
        val = get_secret("DEEPSEEK_API_KEY", secrets_file=self.paths.secrets_file)
        self.assertEqual(val, "sk-test-key-12345")

    def test_get_secret_fallback_env(self):
        os.environ["CUSTOM_TEST_SECRET"] = "sk-env-fallback"
        try:
            val = get_secret("CUSTOM_TEST_SECRET", secrets_file=self.paths.secrets_file)
            self.assertEqual(val, "sk-env-fallback")
        finally:
            del os.environ["CUSTOM_TEST_SECRET"]

    def test_insecure_permissions_rejected(self):
        os.chmod(self.paths.secrets_file, 0o644)
        with self.assertRaises(InsecurePermissionsError):
            load_secrets(secrets_file=self.paths.secrets_file, base_dir=self.base_dir)

    def test_insecure_symlink_rejected(self):
        outside_temp = tempfile.TemporaryDirectory()
        outside_file = Path(outside_temp.name) / "leaked.env"
        outside_file.write_text("DEEPSEEK_API_KEY=leaked\n", encoding="utf-8")
        os.chmod(outside_file, 0o600)

        symlink_path = self.paths.config_dir / "symlink_secrets.env"
        os.symlink(outside_file, symlink_path)

        with self.assertRaises(UnsafePathError):
            load_secrets(secrets_file=symlink_path, base_dir=self.base_dir)

        outside_temp.cleanup()

    def test_non_utf8_rejected(self):
        with open(self.paths.secrets_file, "wb") as f:
            f.write(b"DEEPSEEK_API_KEY=\xff\xfe\xfd\n")
        os.chmod(self.paths.secrets_file, 0o600)

        with self.assertRaises(InvalidConfigError):
            load_secrets(secrets_file=self.paths.secrets_file, base_dir=self.base_dir)

    def test_missing_file_returns_empty(self):
        non_existent = self.paths.config_dir / "does_not_exist.env"
        res = load_secrets(secrets_file=non_existent, base_dir=self.base_dir)
        self.assertEqual(res, {})


if __name__ == "__main__":
    unittest.main()
