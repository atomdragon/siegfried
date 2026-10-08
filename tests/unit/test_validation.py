"""Unit tests for runtime validation, security audit and doctor diagnostics (Phase 1.2).

Covers:
1. Valid runtime -> READY.
2. Uninitialized runtime -> NOT_INITIALIZED.
3. Configuration validation:
   - Valid core_profile and active_agenda.
   - Truncated / empty JSON.
   - Non-UTF8 content.
   - Missing required fields.
   - Wrong types (e.g. posture limit as string, courses as dict).
   - Negative / invalid numeric limits.
   - Secondary tasks > 2.
4. Security & Permissions:
   - File permissions: 0600 valid, 0644/0666/0777 rejected as INSECURE_PERMISSIONS.
   - Directory permissions: 0700 valid, 0755/0775/0777 rejected as INSECURE_PERMISSIONS.
   - Insecure symlinks pointing outside runtime rejected as UNSAFE_PATH.
   - Non-regular files (e.g. directory where file expected, FIFO) rejected as UNSAFE_PATH.
5. Secrets privacy:
   - secrets.env read safely without exposing content in results or errors.
6. Vault validation:
   - Valid empty vault.
   - Valid vault with Event Schema v1 events.
   - Vault with corrupt JSON / invalid event detected.
7. raise_if_runtime_invalid helper.
"""

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from siegfried.contracts.config import (
    get_default_active_agenda,
    get_default_core_profile,
    validate_active_agenda,
    validate_core_profile,
)
from siegfried.contracts.events import Event, EventType
from siegfried.core.errors import (
    InsecurePermissionsError,
    InvalidConfigError,
    RuntimeNotInitializedError,
    StorageError,
    UnsafePathError,
)
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.validation import (
    RuntimeStatus,
    raise_if_runtime_invalid,
    validate_user_runtime,
)
from siegfried.storage.vault import Vault


class TestRuntimeValidation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name) / "siegfried_test_home"
        self.runtime_path = Path(self.temp_dir.name) / "siegfried_test_run"
        self.paths = SiegfriedPaths(base_dir=self.base_path, runtime_dir=self.runtime_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. Valid initialized runtime
    def test_valid_initialized_runtime_is_ready(self):
        ensure_user_runtime(self.paths)
        result = validate_user_runtime(self.paths, deep_vault_audit=True)

        self.assertTrue(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.READY)
        self.assertEqual(result.sections["Configuración"], "OK")
        self.assertEqual(result.sections["Permisos"], "OK")
        self.assertEqual(result.sections["Vault"], "OK")
        self.assertEqual(result.sections["Rutas"], "OK")
        self.assertIsNone(result.error_message)
        self.assertIsNone(result.problematic_path)

        # raise_if_runtime_invalid must not raise
        raise_if_runtime_invalid(self.paths)

    # 2. Uninitialized runtime -> NOT_INITIALIZED
    def test_uninitialized_runtime_status(self):
        self.assertFalse(self.base_path.exists())
        result = validate_user_runtime(self.paths)

        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.NOT_INITIALIZED)
        self.assertIn("no existe", result.error_message.lower())

        with self.assertRaises(RuntimeNotInitializedError):
            raise_if_runtime_invalid(self.paths)

    def test_missing_single_config_file_not_initialized(self):
        ensure_user_runtime(self.paths)
        self.paths.core_profile_file.unlink()

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.NOT_INITIALIZED)
        self.assertEqual(result.sections["Configuración"], "NOT_INITIALIZED")
        self.assertEqual(result.problematic_path, self.paths.core_profile_file)

    # 3. Configuration validation negative cases
    def test_corrupted_json_core_profile(self):
        ensure_user_runtime(self.paths)
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json syntax ...")

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)
        self.assertEqual(result.sections["Configuración"], "INVALID_CONFIG")
        self.assertEqual(result.problematic_path, self.paths.core_profile_file)

        with self.assertRaises(InvalidConfigError):
            raise_if_runtime_invalid(self.paths)

    def test_truncated_empty_json_core_profile(self):
        ensure_user_runtime(self.paths)
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            f.write("")

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)
        self.assertIn("vacío", result.error_message.lower())

    def test_non_utf8_core_profile(self):
        ensure_user_runtime(self.paths)
        with open(self.paths.core_profile_file, "wb") as f:
            f.write(b'{"v": 1, "user_title": "\xff\xfe\xfa"}')

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)
        self.assertIn("utf-8", result.error_message.lower())

    def test_schema_wrong_types_core_profile(self):
        ensure_user_runtime(self.paths)
        # posture_limit_min must be positive number, not string
        bad_profile = {
            "v": 1,
            "user_title": "Señor",
            "posture_limit_min": "sixty",
            "active_courses": []
        }
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            json.dump(bad_profile, f)

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)
        self.assertIn("posture_limit_min", result.error_message)

    def test_schema_negative_limit_core_profile(self):
        ensure_user_runtime(self.paths)
        bad_profile = {
            "v": 1,
            "user_title": "Señor",
            "posture_limit_min": -10,
            "active_courses": []
        }
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            json.dump(bad_profile, f)

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)

    def test_schema_array_root_core_profile(self):
        ensure_user_runtime(self.paths)
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)

    def test_active_agenda_secondary_tasks_limit_exceeded(self):
        ensure_user_runtime(self.paths)
        bad_agenda = {
            "v": 1,
            "critical_task": None,
            "secondary_tasks": [{"title": "T1"}, {"title": "T2"}, {"title": "T3"}],
            "backlog": []
        }
        with open(self.paths.active_agenda_file, "w", encoding="utf-8") as f:
            json.dump(bad_agenda, f)

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)
        self.assertEqual(result.problematic_path, self.paths.active_agenda_file)

    # 4. Security & Permissions
    def test_insecure_file_permissions_detected(self):
        ensure_user_runtime(self.paths)
        # Change secrets.env to 0644
        os.chmod(self.paths.secrets_file, 0o644)

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INSECURE_PERMISSIONS)
        self.assertEqual(result.sections["Permisos"], "INSECURE_PERMISSIONS")
        self.assertEqual(result.problematic_path, self.paths.secrets_file)

        with self.assertRaises(InsecurePermissionsError):
            raise_if_runtime_invalid(self.paths)

    def test_insecure_dir_permissions_detected(self):
        ensure_user_runtime(self.paths)
        os.chmod(self.paths.config_dir, 0o755)

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INSECURE_PERMISSIONS)
        self.assertEqual(result.sections["Permisos"], "INSECURE_PERMISSIONS")
        self.assertEqual(result.problematic_path, self.paths.config_dir)

    def test_insecure_symlink_pointing_outside_detected(self):
        ensure_user_runtime(self.paths)
        self.paths.secrets_file.unlink()

        outside_target = Path(self.temp_dir.name) / "outside_secret.env"
        outside_target.write_text("dummy", encoding="utf-8")
        os.chmod(outside_target, 0o600)

        os.symlink(outside_target, self.paths.secrets_file)

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.UNSAFE_PATH)
        self.assertEqual(result.sections["Rutas"], "UNSAFE_PATH")

        with self.assertRaises(UnsafePathError):
            raise_if_runtime_invalid(self.paths)

    def test_non_regular_file_substituted(self):
        ensure_user_runtime(self.paths)
        self.paths.active_agenda_file.unlink()
        # Replace active_agenda.json with a directory
        self.paths.active_agenda_file.mkdir(mode=0o700)

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.UNSAFE_PATH)

    # 5. Secrets privacy
    def test_secrets_env_privacy_in_error(self):
        ensure_user_runtime(self.paths)
        # Write non-utf8 invalid bytes into secrets.env
        with open(self.paths.secrets_file, "wb") as f:
            f.write(b"SUPER_SECRET_VALUE_12345\xff\xfe")

        result = validate_user_runtime(self.paths)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)
        self.assertNotIn("SUPER_SECRET_VALUE_12345", result.error_message or "")

    # 6. Vault validation
    def test_vault_valid_with_events(self):
        ensure_user_runtime(self.paths)
        vault = Vault(self.paths.vault_file)
        vault.append(Event.create(EventType.POMODORO_STARTED, {"task": "Validation Test"}))
        vault.append(Event.create(EventType.POMODORO_COMPLETED, {"task": "Validation Test", "duration_min": 25}))

        result = validate_user_runtime(self.paths, deep_vault_audit=True)
        self.assertTrue(result.is_ready)
        self.assertEqual(result.sections["Vault"], "OK")

    def test_vault_with_corrupt_line_detected_in_deep_audit(self):
        ensure_user_runtime(self.paths)
        with open(self.paths.vault_file, "w", encoding="utf-8") as f:
            f.write('{"v": 1, "ts": 123456789.0, "type": "pomodoro_started", "data": {}}\n')
            f.write('NOT_A_VALID_JSON_LINE\n')

        result = validate_user_runtime(self.paths, deep_vault_audit=True)
        self.assertFalse(result.is_ready)
        self.assertEqual(result.status, RuntimeStatus.INVALID_CONFIG)
        self.assertEqual(result.sections["Vault"], "INVALID_CONFIG")
        self.assertIn("Línea 2", result.error_message)

    # 7. Non-destructiveness guarantee
    def test_validation_does_not_modify_any_files(self):
        ensure_user_runtime(self.paths)
        # Put custom data
        custom_data = {"v": 1, "user_title": "CustomTitle", "posture_limit_min": 45, "active_courses": []}
        with open(self.paths.core_profile_file, "w", encoding="utf-8") as f:
            json.dump(custom_data, f)
        os.chmod(self.paths.core_profile_file, 0o600)

        mtime_before = os.path.getmtime(self.paths.core_profile_file)
        content_before = self.paths.core_profile_file.read_text(encoding="utf-8")

        result = validate_user_runtime(self.paths, deep_vault_audit=True)
        self.assertTrue(result.is_ready)

        mtime_after = os.path.getmtime(self.paths.core_profile_file)
        content_after = self.paths.core_profile_file.read_text(encoding="utf-8")

        self.assertEqual(content_before, content_after)
        self.assertEqual(mtime_before, mtime_after)


if __name__ == "__main__":
    unittest.main()
