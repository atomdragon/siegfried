"""F2 regressions: daemon eviction, Cloud wiring and shared inference deadlines."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from siegfried.contracts.inference import InferenceMessage, InferenceRequest
from siegfried.contracts.states import SystemState
from siegfried.core.errors import InferenceDeadlineExceededError
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.inference.cloud import CloudInferenceClient
from siegfried.inference.deadline import read_deadline_chunk
from siegfried.inference.llama_manager import LlamaLifecycleManager, LlamaServerState
from siegfried.inference.local import LocalInferenceClient
from siegfried.inference.orchestrator import InferenceOrchestrator
from siegfried.storage.paths import SiegfriedPaths


class TestInferenceF2Regressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = SiegfriedPaths(base_dir=root, runtime_dir=root / 'run')
        self.now = 1000.0
        self.manager = LlamaLifecycleManager(paths=self.paths, clock=lambda: self.now)
        self.proc = MagicMock()
        self.proc.poll.return_value = None
        self.manager._process = self.proc
        self.manager._state = LlamaServerState.READY

    def daemon(self):
        orchestrator = InferenceOrchestrator(local_manager=self.manager)
        daemon = SiegfriedDaemon(paths=self.paths, orchestrator=orchestrator)
        daemon.ipc_server.poll = MagicMock()
        return daemon

    def test_cloud_constructor_accepts_daemon_arguments(self):
        daemon = SiegfriedDaemon(paths=self.paths)
        with patch('siegfried.storage.secrets.get_secret', return_value='test-key'):
            orchestrator = daemon._get_orchestrator()
        self.assertIsInstance(orchestrator.cloud_client, CloudInferenceClient)
        self.assertEqual(orchestrator.cloud_client._api_key, 'test-key')
        self.assertEqual(orchestrator.cloud_client.secrets_file, self.paths.secrets_file)

    def test_daemon_local_configuration_and_endpoint(self):
        config = {
            'SIEGFRIED_LLAMA_GPU_LAYERS': '16',
            'SIEGFRIED_LLAMA_THREADS': '8',
            'SIEGFRIED_LLAMA_CTX_SIZE': '2048',
            'SIEGFRIED_LLAMA_PORT': '18080',
        }
        daemon = SiegfriedDaemon(paths=self.paths)
        with patch.dict('os.environ', config, clear=True), \
                patch('siegfried.storage.secrets.get_secret', return_value=None), \
                patch('subprocess.Popen') as spawn:
            orchestrator = daemon._get_orchestrator()
        manager = orchestrator.local_manager
        self.assertEqual(manager.binary_path, self.paths.base_dir / 'bin' / 'llama-server')
        self.assertEqual(manager.model_path, self.paths.base_dir / 'models' / 'qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf')
        self.assertEqual((manager.gpu_layers, manager.threads, manager.ctx_size), (16, 8, 2048))
        self.assertEqual(manager.host, '127.0.0.1')
        self.assertEqual(orchestrator.local_client.endpoint_url, 'http://127.0.0.1:18080')
        self.assertEqual(manager.resource_budget.idle_timeout_seconds, 900)
        spawn.assert_not_called()
        with patch.object(manager, 'validate_prerequisites'), \
                patch.object(manager, '_wait_for_health_check', return_value=True), \
                patch('subprocess.Popen', return_value=self.proc) as spawn:
            manager.ensure_started(deadline=time.monotonic() + 10)
        args = spawn.call_args.args[0]
        for flag, value in [('--n-gpu-layers', '16'), ('--threads', '8'), ('--ctx-size', '2048'),
                            ('--port', '18080'), ('--host', '127.0.0.1')]:
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertFalse(spawn.call_args.kwargs['shell'])

    def test_daemon_configuration_preserves_cpu_default(self):
        daemon = SiegfriedDaemon(paths=self.paths)
        with patch.dict('os.environ', {}, clear=True), \
                patch('siegfried.storage.secrets.get_secret', return_value=None):
            orchestrator = daemon._get_orchestrator()
        self.assertEqual(orchestrator.local_manager.gpu_layers, 0)

    def test_daemon_configuration_accepts_explicit_model_and_binary(self):
        config = {'SIEGFRIED_LLAMA_BINARY': str(self.paths.base_dir / 'custom-server'),
                  'SIEGFRIED_LLAMA_MODEL': str(self.paths.base_dir / 'custom.gguf')}
        daemon = SiegfriedDaemon(paths=self.paths)
        with patch.dict('os.environ', config, clear=True), \
                patch('siegfried.storage.secrets.get_secret', return_value=None):
            manager = daemon._get_orchestrator().local_manager
        self.assertEqual(manager.binary_path, Path(config['SIEGFRIED_LLAMA_BINARY']))
        self.assertEqual(manager.model_path, Path(config['SIEGFRIED_LLAMA_MODEL']))

    def test_idle_tick_evicts_at_exactly_900_seconds(self):
        daemon = self.daemon()
        self.now = 1899.999
        daemon.run_tick(0)
        self.proc.terminate.assert_not_called()
        self.now = 1900.0
        daemon.run_tick(0)
        self.proc.terminate.assert_called_once()
        self.assertEqual(self.manager.state, LlamaServerState.STOPPED)

    def test_tick_preserves_active_inference(self):
        daemon = self.daemon()
        with self.manager.active_inference():
            self.now = 2000.0
            daemon.run_tick(0)
            self.proc.terminate.assert_not_called()
            self.assertEqual(self.manager.active_requests, 1)

    def test_tick_preserves_server_outside_idle(self):
        daemon = self.daemon()
        daemon.state_machine.transition_to(SystemState.POMODORO_RUNNING)
        self.now = 2000.0
        daemon.run_tick(0)
        self.proc.terminate.assert_not_called()

    def test_tick_does_not_initialize_inference(self):
        daemon = SiegfriedDaemon(paths=self.paths)
        with patch.object(daemon.ipc_server, 'poll'), patch.object(daemon, '_init_orchestrator') as init:
            daemon.run_tick(0)
        init.assert_not_called()

    def test_tick_accepts_an_orchestrator_without_local_manager(self):
        daemon = SiegfriedDaemon(paths=self.paths, orchestrator=SimpleNamespace())
        with patch.object(daemon.ipc_server, 'poll'):
            daemon.run_tick(0)

    def test_tick_uses_the_local_client_manager_when_needed(self):
        orchestrator = InferenceOrchestrator(local_client=LocalInferenceClient(manager=self.manager))
        daemon = SiegfriedDaemon(paths=self.paths, orchestrator=orchestrator)
        self.now = 1900.0
        with patch.object(daemon.ipc_server, 'poll'):
            daemon.run_tick(0)
        self.proc.terminate.assert_called_once()

    def test_expired_startup_does_not_spawn(self):
        self.manager._process = None
        self.manager._state = LlamaServerState.STOPPED
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(InferenceDeadlineExceededError):
                self.manager.ensure_started(deadline=time.monotonic() - 1)
        spawn.assert_not_called()

    def test_startup_health_polling_uses_remaining_deadline(self):
        self.manager._process = None
        self.manager._state = LlamaServerState.STOPPED
        clock = [100.0]
        timeouts = []
        sleeps = []

        def fail_probe(req, timeout):
            timeouts.append(timeout)
            clock[0] += min(timeout, 0.01)
            raise urllib.error.URLError('loading')

        def advance(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        with patch('time.monotonic', side_effect=lambda: clock[0]), \
                patch('time.sleep', side_effect=advance), \
                patch.object(self.manager, 'validate_prerequisites'), \
                patch('subprocess.Popen', return_value=self.proc), \
                patch('urllib.request.urlopen', side_effect=fail_probe):
            with self.assertRaises(InferenceDeadlineExceededError):
                self.manager.ensure_started(deadline=100.045)
        self.assertAlmostEqual(timeouts[0], 0.045)
        self.assertAlmostEqual(timeouts[1], 0.015)
        self.assertLessEqual(sum(sleeps), 0.025 + 1e-9)
        self.assertEqual(self.manager.state, LlamaServerState.STOPPED)
        self.proc.terminate.assert_called_once()
        self.proc.wait.assert_called_once_with(timeout=0.0)

    def test_http_timeout_is_recomputed_after_startup(self):
        clock = [100.0]
        self.manager._process = None
        self.manager._state = LlamaServerState.STOPPED

        def start(deadline):
            self.assertEqual(deadline, 110.0)
            clock[0] = 106.0
            self.manager._process = self.proc
            self.manager._state = LlamaServerState.READY

        response = MagicMock()
        response.status = 200
        response.fp = None
        response.read1.side_effect = [b'{"content":"ok"}', b'']
        opener_response = MagicMock()
        opener_response.__enter__.return_value = response
        client = LocalInferenceClient(manager=self.manager)
        request = InferenceRequest(messages=[InferenceMessage(role='user', content='hi')], timeout_seconds=10)
        with patch('time.monotonic', side_effect=lambda: clock[0]), \
                patch.object(self.manager, 'ensure_started', side_effect=start), \
                patch('urllib.request.urlopen', return_value=opener_response) as opened:
            self.assertEqual(client.generate(request).content, 'ok')
        self.assertEqual(opened.call_args.kwargs['timeout'], 4.0)

    def test_socket_timeout_shrinks_before_each_read(self):
        sock = MagicMock()
        sock.gettimeout.side_effect = [10.0, 0.5]
        response = SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=sock)),
                                   read=MagicMock(), read1=MagicMock(return_value=b'x'))
        with patch('time.monotonic', side_effect=[100.0, 100.1, 100.4, 100.45]):
            read_deadline_chunk(response, 100, 100.5)
            read_deadline_chunk(response, 100, 100.5)
        self.assertAlmostEqual(sock.settimeout.call_args_list[0].args[0], 0.5)
        self.assertAlmostEqual(sock.settimeout.call_args_list[1].args[0], 0.1)
        response.read.assert_not_called()

    def test_health_body_reads_share_startup_deadline(self):
        response = MagicMock()
        response.status = 200
        response.fp = None
        response.read1.side_effect = [b'{"status":"ok"}', b'']
        opened = MagicMock()
        opened.__enter__.return_value = response
        with patch('urllib.request.urlopen', return_value=opened), \
                patch('siegfried.inference.llama_manager.read_deadline_chunk', wraps=read_deadline_chunk) as read:
            deadline = time.monotonic() + 0.1
            self.assertTrue(self.manager._wait_for_health_check(deadline=deadline))
        self.assertEqual(read.call_args.args[2], deadline)

    def test_expired_startup_cleanup_does_not_wait_extra_seconds(self):
        self.manager._state = LlamaServerState.STARTING
        self.proc.wait.side_effect = subprocess.TimeoutExpired('llama-server', 0)
        with patch.object(self.manager, '_wait_for_health_check', return_value=False):
            with self.assertRaises(InferenceDeadlineExceededError):
                # The initial deadline is valid; health polling consumes the budget.
                with patch('time.monotonic', side_effect=[100.0, 100.0, 101.0]):
                    self.manager.ensure_started(deadline=100.5)
        self.proc.kill.assert_called_once()
        self.assertEqual([call.kwargs['timeout'] for call in self.proc.wait.call_args_list], [0.0, 0.0])

    def test_slow_http_body_obeys_deadline_for_both_clients(self):
        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                body = json.dumps({'choices': [{'message': {'content': 'ok'}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        worker.start()
        try:
            endpoint = f'http://127.0.0.1:{server.server_port}'
            clients = [LocalInferenceClient(endpoint_url=endpoint),
                       CloudInferenceClient(api_key='test-key', endpoint_url=endpoint)]
            for client in clients:
                with self.subTest(client=type(client).__name__):
                    started = time.monotonic()
                    request = InferenceRequest(messages=[InferenceMessage(role='user', content='hi')],
                                               deadline=started + 0.12, timeout_seconds=10)
                    with self.assertRaises(InferenceDeadlineExceededError):
                        client.generate(request)
                    self.assertLess(time.monotonic() - started, 0.4)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=1)


if __name__ == '__main__':
    unittest.main()
