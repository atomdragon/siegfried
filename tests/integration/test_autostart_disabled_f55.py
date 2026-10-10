"""Initial disabled publication; never execute installed programs or real HOME."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools import install_boot_briefing as installer
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths


class DisabledAutostartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='siegfried-disabled-')
        self.home = Path(self.temp.name)
        self.runtime = self.home / '.siegfried'
        ensure_user_runtime(SiegfriedPaths(self.runtime, self.home / 'run'))
        self.package = installer.prepare_package(self.home, installer.REPO_ROOT, False)
        self.entry = self.home / '.config/autostart' / installer.ENTRY
        self.before = self.bytes_state()

    def tearDown(self):
        self.temp.cleanup()

    def bytes_state(self):
        return {str(p.relative_to(self.home)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_ino, p.stat().st_mode)
                for base in (self.runtime, self.package) for p in base.rglob('*') if p.is_file()}

    def install(self, **kwargs):
        return installer.manage_autostart(self.home, enabled=False, **kwargs)

    def create_entry(self, text):
        self.entry.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.entry.write_text(text)
        self.entry.chmod(0o600)

    def test_first_publication_is_disabled_secure_and_never_executes(self):
        original_link = os.link
        published = []
        def observe(src, dst, **kwargs):
            # Observe exact staged bytes at the instant of first publication.
            fd = os.open(src, os.O_RDONLY, dir_fd=kwargs['src_dir_fd'])
            try: content = os.read(fd, 8192).decode()
            finally: os.close(fd)
            self.assertIn('Hidden=true\n', content)
            self.assertNotIn('Hidden=false', content)
            published.append(content)
            return original_link(src, dst, **kwargs)
        with patch.object(installer.os, 'link', side_effect=observe), patch('subprocess.Popen') as spawned:
            self.install(dry_run=False)
            spawned.assert_not_called()
        self.assertEqual(len(published), 1)
        s = self.entry.stat()
        self.assertEqual((s.st_uid, s.st_gid, s.st_mode & 0o777, s.st_nlink), (os.getuid(), os.getgid(), 0o600, 1))
        self.assertEqual(self.before, self.bytes_state())
        self.assertEqual(list(self.entry.parent.iterdir()), [self.entry])
        result = subprocess.run(['desktop-file-validate', str(self.entry)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.entry.read_text()
        self.assertIn('Terminal=false\n', content)
        self.assertIn('OnlyShowIn=KDE;\n', content)
        self.assertIn(str(self.package / 'scripts/boot_hook.py'), content)
        self.assertNotIn(str(installer.REPO_ROOT), content)

    def test_cli_disabled_dry_run_no_publication(self):
        p = subprocess.run([sys.executable, '-B', str(installer.REPO_ROOT / 'tools/install_boot_briefing.py'), '--home', str(self.home), '--disabled', '--dry-run'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Hidden=true\n', p.stdout)
        self.assertFalse(self.entry.exists())
        self.assertEqual(self.before, self.bytes_state())

    def test_cli_disabled_apply_in_fixture(self):
        p = subprocess.run([sys.executable, '-B', str(installer.REPO_ROOT / 'tools/install_boot_briefing.py'), '--home', str(self.home), '--disabled', '--apply'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Hidden=true\n', self.entry.read_text())
        self.assertEqual(self.before, self.bytes_state())

    def test_idempotence_preserves_file_and_timestamps(self):
        self.install(dry_run=False)
        content = self.entry.read_bytes();before = self.entry.stat()
        result = self.install(dry_run=False)
        after = self.entry.stat()
        self.assertTrue(result['unchanged'])
        self.assertEqual(content, self.entry.read_bytes())
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_ctime_ns), (after.st_ino, after.st_mtime_ns, after.st_ctime_ns))

    def test_enabled_default_remains_enabled_and_is_not_overwritten(self):
        installer.manage_autostart(self.home, dry_run=False)
        content = self.entry.read_bytes();ino = self.entry.stat().st_ino
        self.assertIn(b'Hidden=false', content)
        with self.assertRaises(ValueError): self.install(dry_run=False)
        self.assertEqual((content, ino), (self.entry.read_bytes(), self.entry.stat().st_ino))

    def test_foreign_entry_preserved(self):
        self.create_entry('[Desktop Entry]\nType=Application\nName=Foreign\n')
        content = self.entry.read_bytes()
        with self.assertRaises(ValueError): self.install(dry_run=False)
        self.assertEqual(content, self.entry.read_bytes())

    def test_entry_symlink_preserved(self):
        self.entry.parent.mkdir(parents=True, mode=0o700)
        target = self.home / 'foreign';target.write_text('unchanged')
        self.entry.symlink_to(target)
        with self.assertRaises((OSError, ValueError)): self.install(dry_run=False)
        self.assertTrue(self.entry.is_symlink())
        self.assertEqual(target.read_text(), 'unchanged')

    def test_concurrent_foreign_publication_is_never_replaced(self):
        original_link = os.link
        def race(src, dst, **kwargs):
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs['dst_dir_fd'])
            os.write(fd, b'foreign-race');os.close(fd)
            return original_link(src, dst, **kwargs)
        with patch.object(installer.os, 'link', side_effect=race):
            with self.assertRaises(FileExistsError): self.install(dry_run=False)
        self.assertEqual(self.entry.read_bytes(), b'foreign-race')
        self.assertEqual(list(self.entry.parent.iterdir()), [self.entry])

    def test_owned_entry_rollback_preserves_existing_snapshot_and_runtime(self):
        self.install(dry_run=False)
        data = self.entry.read_bytes();info = self.entry.stat()
        self.assertIn(installer.OWNER_TAG.encode(), data)
        self.assertEqual(self.entry.stat().st_ino, info.st_ino)
        self.entry.unlink()  # Recorded own entry only; never full installer uninstall.
        self.assertFalse(self.entry.exists())
        self.assertEqual(self.before, self.bytes_state())

    def test_failed_publication_cleans_staging_without_touching_data(self):
        with patch.object(installer.os, 'link', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError): self.install(dry_run=False)
        self.assertFalse(self.entry.exists())
        self.assertEqual(list(self.entry.parent.iterdir()), [])
        self.assertEqual(self.before, self.bytes_state())

    def test_disabled_flag_rejects_activation_operations(self):
        for operation in ('enable', 'disable', 'uninstall'):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError): self.install(operation=operation, dry_run=False)
        self.assertFalse(self.entry.exists())
