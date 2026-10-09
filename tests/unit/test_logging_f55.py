"""Logging levels, append-only private files and defensive formatting in fixtures."""
import io
import json
import logging
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from siegfried.observability.logging import StructuredFormatter, setup_logger


class TestLoggingF55(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='siegfried-log-test-')
        self.base = Path(self.temp.name)
        self.directory = self.base / 'logs'
        self.component = 'f55_logging_fixture'
        self.path = self.directory / f'{self.component}.log'
        self.console = io.StringIO()
        self.stderr_patch = patch('sys.stderr', self.console)
        self.stderr_patch.start()

    def tearDown(self):
        logger = logging.getLogger(f'siegfried.{self.component}')
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        self.stderr_patch.stop()
        self.temp.cleanup()

    def setup(self, **kwargs):
        return setup_logger(self.component, self.directory, **kwargs)

    def test_info_warning_error_reach_file_and_console_debug_does_not(self):
        logger = self.setup()
        logger.debug('debug-marker')
        for level in (logging.INFO, logging.WARNING, logging.ERROR):
            logger.log(level, f'level-{level}')
        for output in (self.path.read_text(), self.console.getvalue()):
            self.assertNotIn('debug-marker', output)
            for level in (logging.INFO, logging.WARNING, logging.ERROR):
                self.assertIn(f'level-{level}', output)

    def test_warning_level_filters_info(self):
        logger = self.setup(level=logging.WARNING)
        logger.info('filtered-info')
        logger.warning('visible-warning')
        logger.error('visible-error')
        self.assertNotIn('filtered-info', self.path.read_text())
        self.assertIn('visible-warning', self.path.read_text())
        self.assertIn('visible-error', self.path.read_text())

    def test_error_level_filters_warning(self):
        logger = self.setup(level=logging.ERROR)
        logger.warning('filtered-warning')
        logger.error('visible-error')
        self.assertNotIn('filtered-warning', self.path.read_text())
        self.assertIn('visible-error', self.path.read_text())

    def test_private_modes_even_with_permissive_umask(self):
        previous = os.umask(0)
        try:
            self.setup().info('private operational marker')
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.path.stat().st_uid, os.getuid())

    def test_reconfiguration_appends_without_duplicate_handlers_or_truncation(self):
        logger = self.setup()
        logger.info('first-run')
        first = self.path.read_bytes()
        inode = self.path.stat().st_ino
        old_streams = [handler.stream for handler in logger.handlers if isinstance(handler, logging.FileHandler)]
        logger = self.setup()
        logger.info('second-run')
        content = self.path.read_bytes()
        self.assertTrue(content.startswith(first))
        self.assertEqual(content.count(b'first-run'), 1)
        self.assertEqual(content.count(b'second-run'), 1)
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(len(logger.handlers), 2)
        self.assertTrue(all(stream.closed for stream in old_streams))

    def test_insecure_existing_log_is_preserved_and_not_chmodded(self):
        self.directory.mkdir(mode=0o700)
        self.path.write_bytes(b'prior-data')
        self.path.chmod(0o644)
        before = self.path.stat()
        logger = self.setup()
        logger.info('console-only')
        after = self.path.stat()
        self.assertEqual(self.path.read_bytes(), b'prior-data')
        self.assertEqual((after.st_mode, after.st_ino, after.st_mtime_ns, after.st_ctime_ns),
                         (before.st_mode, before.st_ino, before.st_mtime_ns, before.st_ctime_ns))
        self.assertIn('Archivo de log privado no disponible.', self.console.getvalue())

    def test_file_symlink_never_appends_to_target(self):
        self.directory.mkdir(mode=0o700)
        target = self.base / 'foreign'
        target.write_bytes(b'untouched')
        self.path.symlink_to(target)
        self.setup().info('do not follow')
        self.assertEqual(target.read_bytes(), b'untouched')
        self.assertTrue(self.path.is_symlink())

    def test_directory_symlink_is_rejected(self):
        target = self.base / 'foreign-directory'
        target.mkdir(mode=0o700)
        self.directory.symlink_to(target, target_is_directory=True)
        self.setup().info('do not follow parent')
        self.assertEqual(list(target.iterdir()), [])

    def test_hardlinked_existing_log_is_preserved(self):
        self.directory.mkdir(mode=0o700)
        target = self.base / 'foreign'
        target.write_bytes(b'untouched')
        target.chmod(0o600)
        os.link(target, self.path)
        self.setup().info('do not append through hardlink')
        self.assertEqual(target.read_bytes(), b'untouched')

    def test_fifo_is_rejected_without_waiting_for_reader(self):
        self.directory.mkdir(mode=0o700)
        os.mkfifo(self.path, 0o600)
        self.setup().warning('console-only')
        self.assertTrue(stat.S_ISFIFO(self.path.stat().st_mode))
        self.assertIn('console-only', self.console.getvalue())

    def test_insecure_directory_is_not_modified(self):
        self.directory.mkdir(mode=0o755)
        self.directory.chmod(0o755)
        self.setup().info('console-only')
        self.assertFalse(self.path.exists())
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o755)

    def test_sensitive_structured_fields_and_nested_sequences_are_redacted(self):
        logger = self.setup(json_output=True)
        logger.info('Operational result', extra={'structured_data': {
            'prompt': 'synthetic-private-prompt', 'task_title': 'synthetic-private-title',
            'entries': [{'token': 'synthetic-token', 'value': 'sk-synthetic123'},
                        {'password': 'synthetic-password'}], 'count': 2}})
        output = self.path.read_text()
        for value in ('synthetic-private-prompt', 'synthetic-private-title', 'synthetic-token',
                      'sk-synthetic123', 'synthetic-password'):
            self.assertNotIn(value, output)
        data = json.loads(output)['data']
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['prompt'], '[REDACTED]')
        self.assertEqual(data['entries'][0]['value'], '[REDACTED]')

    def test_message_credentials_and_labeled_prompt_are_redacted(self):
        logger = self.setup()
        for message in ('Engine key sk-synthetic123', 'Engine Bearer synthetic-token',
                        'api_key=synthetic-assignment', 'DEEPSEEK_API_KEY=synthetic-env-value',
                        'prompt: synthetic private text\ncontinuation',
                        '-----BEGIN PRIVATE KEY-----\nsynthetic-pem\n-----END PRIVATE KEY-----',
                        '-----BEGIN PRIVATE KEY-----\nsynthetic-incomplete-pem'):
            logger.error(message)
        for output in (self.path.read_text(), self.console.getvalue()):
            for value in ('sk-synthetic123', 'synthetic-token', 'synthetic-assignment',
                          'synthetic private text', 'continuation', 'synthetic-pem',
                          'synthetic-env-value', 'synthetic-incomplete-pem'):
                self.assertNotIn(value, output)

    def test_json_exception_does_not_serialize_private_traceback(self):
        logger = self.setup(json_output=True)
        try:
            raise RuntimeError('synthetic-private-exception-prompt')
        except RuntimeError:
            logger.exception('Operation failed')
        output = self.path.read_text()
        self.assertNotIn('synthetic-private-exception-prompt', output)
        self.assertEqual(json.loads(output)['msg'], 'Operation failed')

    def test_record_cannot_inject_an_extra_line(self):
        self.setup().warning('Operational\nforged\rrecord\0')
        output = self.path.read_text()
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn(r'Operational\nforged\rrecord\0', output)

    def test_component_cannot_escape_log_directory(self):
        with self.assertRaises(ValueError):
            setup_logger('../foreign', self.directory)
        self.assertFalse((self.base / 'foreign.log').exists())


if __name__ == '__main__':
    unittest.main()
