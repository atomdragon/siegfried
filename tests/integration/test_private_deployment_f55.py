"""Private deployment invariants; fixtures never enable the user's service."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tools import install_user_service as service
from tools import install_boot_briefing as boot
from siegfried.contracts.ipc import IPCCommand, IPCStatus
from siegfried.ipc.client import IPCClient
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths


class TestPrivateDeploymentF55(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='siegfried-f55-')
        self.home = Path(self.tmp.name)
        self.package = self.home / '.local/share/siegfried-daemon'
        self.unit = self.home / '.config/systemd/user/siegfried.service'

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, **kwargs):
        return service.install_user_service(self.home, **kwargs)

    def create_service_directory(self):
        for relative in ('.config', '.config/systemd', '.config/systemd/user'):
            (self.home / relative).mkdir(mode=0o700, exist_ok=True)

    def test_dry_run_and_install_without_activation(self):
        self.install(dry_run=True)
        self.assertEqual(list(self.home.iterdir()), [])
        with patch.object(service.subprocess, 'run', wraps=subprocess.run) as run:
            self.install()
        self.assertTrue(all(call.args[0][0] == 'systemd-analyze' for call in run.call_args_list))
        self.assertIn(b'/usr/bin/python3 -B', self.unit.read_bytes())
        self.assertNotIn(b'/media/', self.unit.read_bytes())
        self.assertFalse((self.home / '.config/systemd/user/default.target.wants').exists())

    def test_sources_hashes_modes_resources_and_no_private_data(self):
        self.install()
        sources = boot.package_sources(service.REPO_ROOT, daemon=True)
        self.assertIn('bin/siegfried-daemon', sources)
        self.assertIn('scripts/kwin_focus_watcher/contents/code/main.js', sources)
        for relative, expected in sources.items():
            target = self.package / relative
            self.assertEqual(hashlib.sha256(target.read_bytes()).digest(), hashlib.sha256(expected).digest())
            self.assertEqual(target.stat().st_mode & 0o777, 0o700 if relative in ('bin/siegfried', 'bin/siegfried-daemon', 'scripts/boot_hook.py') else 0o600)
        self.assertFalse(any(p.name in ('.git', '__pycache__', 'secrets.env', '.history') or p.suffix in ('.gguf', '.jsonl') for p in self.package.rglob('*')))
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o700 for p in self.package.rglob('*') if p.is_dir()))

    def test_idempotence_and_rollback_preserve_runtime_and_briefing(self):
        paths = SiegfriedPaths(self.home / '.siegfried', self.home / 'run')
        ensure_user_runtime(paths)
        before = {p: p.read_bytes() for p in paths.base_dir.rglob('*') if p.is_file()}
        boot.manage_autostart(self.home, dry_run=False)
        self.install()
        manifest = {p: (p.stat().st_ino, p.read_bytes()) for p in service.deployment_files(self.home)}
        self.install()
        self.assertEqual(manifest, {p: (p.stat().st_ino, p.read_bytes()) for p in manifest})
        self.install(operation='uninstall')
        self.assertFalse(self.package.exists())
        self.assertFalse(self.unit.exists())
        self.assertTrue((self.home / '.local/share/siegfried-boot/bin/siegfried').exists())
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
        self.install(operation='uninstall')

    def test_briefing_uninstall_preserves_daemon(self):
        self.install()
        boot.manage_autostart(self.home, dry_run=False)
        boot.manage_autostart(self.home, operation='uninstall', dry_run=False)
        self.assertTrue((self.package / 'bin/siegfried-daemon').exists())
        self.assertTrue(self.unit.exists())

    def test_group_writable_service_parent_rejected_before_writes(self):
        parent = self.home / '.config/systemd/user'
        self.create_service_directory()
        parent.chmod(0o775)
        for dry in (True, False):
            with self.assertRaises(ValueError):
                self.install(dry_run=dry)
        self.assertFalse(self.package.exists())
        self.assertEqual(parent.stat().st_mode & 0o777, 0o775)

    def test_symlink_home_and_destination_rejected(self):
        alias = self.home / 'alias'
        alias.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(OSError):
            service.install_user_service(alias)
        self.create_service_directory()
        foreign = self.home / 'foreign'
        foreign.write_bytes(b'fixture')
        self.unit.symlink_to(foreign)
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual(foreign.read_bytes(), b'fixture')
        self.assertFalse(self.package.exists())

    def test_foreign_unit_and_changed_owned_code_preserved(self):
        self.create_service_directory()
        self.unit.write_bytes(b'foreign fixture'); self.unit.chmod(0o600)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.unit.read_bytes(), b'foreign fixture')
        self.assertFalse(self.package.exists())
        self.unit.unlink()  # Fixture only.
        self.install()
        changed = self.package / 'bin/siegfried-daemon'
        changed.write_bytes(b'changed fixture')
        for operation in ('install', 'uninstall'):
            with self.assertRaises(ValueError):
                self.install(operation=operation)
        self.assertTrue(self.unit.exists())
        self.assertEqual(changed.read_bytes(), b'changed fixture')

    def test_unit_verification_failure_is_not_reported_as_success_and_rollback(self):
        with patch.object(service, 'verify_unit', side_effect=ValueError('fixture')):
            with self.assertRaises(ValueError):
                self.install()
        self.assertTrue(self.package.exists())
        self.assertFalse(self.unit.exists())
        self.install(operation='uninstall')
        self.assertFalse(self.package.exists())

    def test_concurrent_publication_cannot_overwrite_foreign_file(self):
        destination = self.home / 'fixture'
        destination.write_bytes(b'foreign'); destination.chmod(0o600)
        with self.assertRaises(ValueError):
            service.publish(destination, b'owned', 0o600)
        self.assertEqual(destination.read_bytes(), b'foreign')
        self.assertEqual(list(self.home.iterdir()), [destination])

    def test_path_with_spaces_percent_and_quotes_verifies(self):
        special = self.home / 'fixture % with "quotes"'
        special.mkdir(mode=0o700)
        service.install_user_service(special)
        self.assertTrue((special / '.config/systemd/user/siegfried.service').exists())

    def test_wrappers_and_daemon_import_without_checkout_or_pythonpath(self):
        self.install()
        paths = SiegfriedPaths(self.home / '.siegfried', self.home / 'run')
        ensure_user_runtime(paths)
        env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'SIEGFRIED_HOME':str(paths.base_dir),
               'XDG_RUNTIME_DIR':str(paths.runtime_dir), 'PYTHONDONTWRITEBYTECODE':'1'}
        cli = [sys.executable, '-S', str(self.home / '.siegfried/bin/siegfried')]
        result = subprocess.run(cli + ['doctor'], env=env, cwd='/', capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        child = subprocess.Popen([sys.executable, '-S', str(self.home / '.siegfried/bin/siegfried-daemon')],
                                 env=env, cwd='/', stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 3
            while not paths.socket_file.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIsNone(child.poll())
            for command in (IPCCommand.PING, IPCCommand.STATUS):
                self.assertEqual(IPCClient(paths.socket_file).call(command).status, IPCStatus.OK.value)
            repl = subprocess.run(cli, input=b'status\nayuda\nping\nsalir\n', env=env, cwd='/', capture_output=True, timeout=3)
            self.assertEqual(repl.returncode, 0, repl.stderr)
            self.assertIn(b'Siegfried', repl.stdout)
            self.assertEqual(IPCClient(paths.socket_file).call(IPCCommand.PING).status, IPCStatus.OK.value)
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=2)
        self.assertEqual(child.returncode, 0)
        self.assertFalse(paths.socket_file.exists())


if __name__ == '__main__':
    unittest.main()
