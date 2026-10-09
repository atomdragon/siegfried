"""Verify real-path denial and temporary defaults in the isolated launcher."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'tools/run_isolated_tests.py'


class RegressionIsolationTests(unittest.TestCase):
    def test_cached_paths_and_child_without_python_hook_are_denied(self):
        result = subprocess.run([sys.executable, '-B', str(RUNNER), '--self-test'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(evidence['guard'], 'PASS')
        self.assertEqual(len(evidence['denied']), 7)
        self.assertTrue(evidence['child_without_site_hook_denied'])
        self.assertTrue(evidence['temporary_home_writable'])

    def test_import_time_defaults_and_repl_use_temporary_runtime(self):
        with tempfile.TemporaryDirectory() as fixture:
            script = Path(fixture) / 'probe.py'
            script.write_text('''import os
from pathlib import Path
from siegfried.storage.paths import SiegfriedPaths
CAPTURED = SiegfriedPaths()
assert CAPTURED.base_dir == Path.home() / '.siegfried'
assert CAPTURED.runtime_dir == Path(os.environ['XDG_RUNTIME_DIR'])
assert str(Path.home()).startswith('/tmp/siegfried-isolated-tests-')
assert CAPTURED.history_file.is_relative_to(Path.home())
assert CAPTURED.base_dir.is_dir()
assert 'DBUS_SESSION_BUS_ADDRESS' not in os.environ
assert 'WAYLAND_DISPLAY' not in os.environ
print('temporary_defaults_PASS')
''')
            result = subprocess.run([sys.executable, '-B', str(RUNNER), str(script)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('temporary_defaults_PASS', result.stdout)
