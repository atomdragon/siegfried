"""Operational deployment failures and real private-process pilot; no host writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from tools import install_boot_briefing as installer
from siegfried.contracts.ipc import IPCCommand, IPCStatus
from siegfried.ipc.client import IPCClient
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths

REPO = Path(__file__).resolve().parents[2]


class TestOperationalPilotF54(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='siegfried-f54-')
        self.home = Path(self.tmp.name)
        self.package = self.home / '.local/share/siegfried-boot'
        self.entry = self.home / '.config/autostart' / installer.ENTRY

    def tearDown(self):
        self.tmp.cleanup()

    def publication_failure(self):
        with patch.object(installer.os, 'replace', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                installer.manage_autostart(self.home, dry_run=False)
        self.assertTrue(self.package.exists())
        self.assertFalse(self.entry.exists())

    def test_partial_install_rollback_without_entry(self):
        self.publication_failure()
        installer.manage_autostart(self.home, operation='uninstall', dry_run=False)
        self.assertFalse(self.package.exists())
        self.assertFalse(self.entry.exists())
        self.assertFalse(list(self.entry.parent.glob('.*.desktop.*')))

    def test_partial_install_retry_and_rollback_preserve_other_entry(self):
        self.publication_failure()
        other = self.entry.parent / 'fixture.desktop'
        other.write_text('[Desktop Entry]\nType=Application\nName=Fixture\nExec=/usr/bin/true\n')
        other.chmod(0o600)
        expected = other.read_bytes()
        installer.manage_autostart(self.home, dry_run=False)
        subprocess.run(['desktop-file-validate', str(self.entry)], check=True, timeout=2)
        installer.manage_autostart(self.home, operation='uninstall', dry_run=False)
        self.assertEqual(other.read_bytes(), expected)

    def test_orphan_with_foreign_content_is_preserved(self):
        self.publication_failure()
        extra = self.package / 'foreign.txt'
        extra.write_text('fixture'); extra.chmod(0o600)
        with self.assertRaises(ValueError):
            installer.manage_autostart(self.home, operation='uninstall', dry_run=False)
        self.assertEqual(extra.read_text(), 'fixture')

    def test_orphan_snapshot_symlink_is_preserved(self):
        target = self.home / 'foreign'; target.mkdir(mode=0o700)
        local = self.home / '.local/share'; local.mkdir(parents=True, mode=0o700)
        self.package.symlink_to(target)
        with self.assertRaises((OSError, ValueError)):
            installer.manage_autostart(self.home, operation='uninstall', dry_run=False)
        self.assertTrue(self.package.is_symlink()); self.assertTrue(target.exists())

    def test_dry_run_rejects_shared_snapshot_ancestors_without_writes(self):
        local = self.home / '.local'; local.mkdir(mode=0o700); local.chmod(0o775)
        with self.assertRaises(ValueError):
            installer.manage_autostart(self.home)
        self.assertEqual(list(self.home.iterdir()), [local])
        self.assertEqual(local.stat().st_mode & 0o777, 0o775)

    def test_0755_application_directories_are_compatible(self):
        for directory in (self.home/'.config/autostart', self.home/'.local/share'):
            directory.mkdir(parents=True, mode=0o755)
            directory.parent.chmod(0o755); directory.chmod(0o755)
        installer.manage_autostart(self.home, dry_run=False)
        self.assertEqual(self.package.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.entry.stat().st_mode & 0o777, 0o600)

    def test_snapshot_excludes_noncode_and_detects_tampering(self):
        installer.manage_autostart(self.home, dry_run=False)
        sources = installer.package_sources(REPO)
        self.assertTrue(all(n.endswith('.py') or n == 'bin/siegfried' for n in sources))
        self.assertTrue(all((self.package/n).read_bytes() == data for n, data in sources.items()))
        self.assertFalse(any(p.suffix in ('.jsonl', '.gguf', '.env') for p in self.package.rglob('*')))
        source = self.package / 'src/siegfried/core/rest.py'
        source.write_text('invalid fixture')
        with self.assertRaises(ValueError):
            installer.manage_autostart(self.home, dry_run=False)
        self.assertEqual(source.read_text(), 'invalid fixture')

    def test_private_snapshot_hook_headless_offline_releases_process(self):
        installer.manage_autostart(self.home, dry_run=False)
        runtime = self.home/'run'; runtime.mkdir(mode=0o700)
        env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8',
               'SIEGFRIED_HOME':str(self.home/'fixture-runtime'), 'XDG_RUNTIME_DIR':str(runtime),
               'PYTHONDONTWRITEBYTECODE':'1'}
        cmd = [sys.executable, '-S', str(self.package/'scripts/boot_hook.py')]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Abra siegfried', result.stdout)
        self.assertNotIn('descanso', result.stdout)
        self.assertFalse((self.home/'fixture-runtime').exists())

    def test_manual_verifier_never_invokes_action_without_human(self):
        from tools import verify_boot_briefing_f53 as verifier
        presenter = MagicMock()
        presenter.matches = []
        presenter.notification_id = 1
        presenter.inhibited = False
        connection = MagicMock()
        connection.get_is_connected.return_value = False
        presenter.bus = connection
        def close():
            presenter.bus = None
        presenter.close.side_effect = close
        with patch.object(verifier, 'KDEBriefingPresenter', return_value=presenter), \
                patch.object(verifier.REPLLauncher, 'available', return_value=True), \
                patch.object(verifier.subprocess, 'run') as launch, patch('builtins.print'):
            result = verifier.verify(manual=True)
        self.assertEqual(result['action_count'], 0)
        self.assertEqual(result['native_action'], 'PENDING_HUMAN_ACTION')
        presenter.GLib.timeout_add.assert_not_called()
        launch.assert_not_called()
        presenter.wait.assert_called_once_with(21)
        presenter.close.assert_called_once()

    def test_private_snapshot_daemon_ipc_restart_and_briefing_probe(self):
        installer.manage_autostart(self.home, dry_run=False)
        paths = SiegfriedPaths(self.home/'fixture-runtime', self.home/'run')
        ensure_user_runtime(paths)
        env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8',
               'SIEGFRIED_HOME':str(paths.base_dir), 'XDG_RUNTIME_DIR':str(paths.runtime_dir),
               'PYTHONPATH':str(self.package/'src'), 'PYTHONDONTWRITEBYTECODE':'1'}
        # No bus, credentials, models or opt-in KDE adapters inherited.
        for cycle in range(2):
            child = subprocess.Popen([sys.executable, '-S', '-m', 'siegfried.daemon'],
                                     env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 3
                while not paths.socket_file.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)  # Bounded fixture readiness, not runtime session polling.
                self.assertIsNone(child.poll())
                self.assertEqual(paths.socket_file.stat().st_mode & 0o777, 0o600)
                reply = IPCClient(paths.socket_file, timeout_seconds=1).call(IPCCommand.PING)
                self.assertEqual(reply.status, IPCStatus.OK.value)
                self.assertTrue(reply.payload['pong'])
                probe = subprocess.run([sys.executable, '-S', str(self.package/'scripts/boot_hook.py'),
                                        '--probe-worker', '--socket-path', str(paths.socket_file)],
                                       env=env, capture_output=True, timeout=2)
                self.assertEqual(probe.stdout, b'READY\n')
            finally:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=2)
            self.assertEqual(child.returncode, 0)
            self.assertFalse(paths.socket_file.exists())
        self.assertEqual(paths.vault_file.stat().st_mode & 0o777, 0o600)
        for line in paths.vault_file.read_text().splitlines():
            event = json.loads(line)
            self.assertEqual(event['v'], 1)


if __name__ == '__main__':
    unittest.main()
