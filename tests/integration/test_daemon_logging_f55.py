"""Operational daemon logging and privacy on initialized temporary runtimes only."""
import io
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from siegfried.contracts.ipc import IPCCommand, IPCRequest
from siegfried.core.errors import StorageError
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.integrations.audio import StubAudioPlayer
from siegfried.integrations.notifications import StubNotificationSender
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths


class TestDaemonLoggingF55(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='siegfried-daemon-log-test-')
        base = Path(self.temp.name)
        self.paths = SiegfriedPaths(base / 'runtime', base / 'run')
        ensure_user_runtime(self.paths)
        self.env = patch.dict('os.environ', {'SIEGFRIED_ENABLE_KWIN': '0', 'SIEGFRIED_ENABLE_SESSION': '0'})
        self.env.start()
        self.console = io.StringIO()
        self.stderr = patch('sys.stderr', self.console)
        self.stderr.start()
        self.log = self.paths.logs_dir / 'daemon.log'

    def daemon(self):
        return SiegfriedDaemon(paths=self.paths, notifier=StubNotificationSender(),
                               audio_player=StubAudioPlayer())

    def tearDown(self):
        logger = logging.getLogger('siegfried.daemon')
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        self.stderr.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_temporary_daemon_restart_logs_operational_messages_and_preserves_data(self):
        before = {p: p.read_bytes() for p in self.paths.base_dir.rglob('*') if p.is_file()}
        first = b''
        for index in range(2):
            daemon = self.daemon()
            try:
                daemon.start()
                for cmd in (IPCCommand.PING, IPCCommand.STATUS):
                    self.assertEqual(daemon.handle_ipc_request(IPCRequest.create(cmd)).status, 'OK')
            finally:
                self.assertTrue(daemon.stop())
            content = self.log.read_bytes()
            self.assertTrue(content.startswith(first))
            first = content
            self.assertFalse(self.paths.socket_file.exists())
            self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)
            for message in ('Iniciando Siegfried Daemon...', 'Siegfried Daemon activo en socket',
                            'Deteniendo Siegfried Daemon...', 'Siegfried Daemon detenido correctamente.'):
                self.assertEqual(content.decode().count(message), index + 1)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_private_task_is_kept_in_domain_data_and_omitted_from_log(self):
        daemon = self.daemon()
        private = 'synthetic personal task with sk-synthetic123'
        try:
            daemon.start()
            response = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.START_FOCUS,
                {'duration_min': 1, 'task': private}))
            self.assertEqual(response.status, 'OK')
            daemon._on_focus_expired()
        finally:
            daemon.stop()
        output = self.log.read_text() + self.console.getvalue()
        self.assertNotIn(private, output)
        self.assertNotIn('sk-synthetic123', output)
        self.assertIn('Bloque de enfoque completado.', output)
        self.assertIn(private, self.paths.vault_file.read_text())

    def test_private_exception_text_never_reaches_daemon_log(self):
        daemon = self.daemon()
        private = 'synthetic-private-prompt Bearer synthetic-token'
        with patch.object(daemon, '_validate_startup_runtime', side_effect=StorageError(private)):
            with self.assertRaises(StorageError):
                daemon.start()
        output = self.log.read_text() + self.console.getvalue()
        self.assertIn('Fallo de seguridad o validación en runtime.', output)
        self.assertNotIn(private, output)
        self.assertNotIn('synthetic-token', output)

    def test_agenda_read_warning_omits_private_exception(self):
        daemon = self.daemon()
        private = 'synthetic personal agenda text'
        with patch('siegfried.storage.atomic_json.read_json_locked', side_effect=RuntimeError(private)):
            daemon._recover_deterministic_state()
        output = self.log.read_text() + self.console.getvalue()
        self.assertIn('No se pudo cargar tarea activa de agenda.', output)
        self.assertNotIn(private, output)

    def test_query_failure_omits_prompt_and_exception_from_logs_without_engine(self):
        daemon = self.daemon()
        private = 'synthetic private medical prompt'
        with patch.object(daemon, '_get_orchestrator', side_effect=RuntimeError(private)):
            response = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.QUERY, {'prompt': private}))
        self.assertEqual(response.status, 'ERROR')
        output = self.log.read_text() + self.console.getvalue()
        self.assertIn('Error inesperado durante inferencia.', output)
        self.assertNotIn(private, output)


if __name__ == '__main__':
    unittest.main()
