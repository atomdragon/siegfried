"""F5.1.1 boundary tests; no session bus or desktop modifications."""
import builtins
import os
import threading
import unittest
from unittest.mock import MagicMock, patch

from siegfried.daemon.focus import FocusDBusAdapter, FocusTracker


class FocusBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tracker = FocusTracker()
        self.adapter = FocusDBusAdapter(self.tracker)
        self.adapter._running = True
        self.adapter._kwin_owner = ':1.16'

    def tearDown(self):
        self.adapter.stop(discard=True)

    def test_other_user_process_cannot_inject_or_change_focus(self):
        self.assertFalse(self.adapter._accept_message('firefox', ':1.999'))
        self.assertFalse(self.adapter._accept_message('firefox', None))
        self.assertEqual(len(self.adapter._pending), 0)
        self.assertIsNone(self.tracker.snapshot()['active_app'])

    def test_reject_size_type_nonascii_and_private_payloads(self):
        for value in ('a' * 65, 'é', '/home/private.txt', 'https://private',
                      'Document Title', 'firefox;cmd', 'private\x00', 123, None):
            with self.subTest(value=value):
                self.assertFalse(self.adapter._accept_message(value, ':1.16'))
        self.assertEqual(len(self.adapter._pending), 0)

    def test_duplicate_does_not_consume_queue_or_tokens(self):
        self.assertTrue(self.adapter._accept_message('firefox', ':1.16'))
        tokens = self.adapter._tokens
        for _ in range(1000):
            self.assertTrue(self.adapter._accept_message('firefox', ':1.16'))
        self.assertEqual(len(self.adapter._pending), 1)
        self.assertEqual(self.adapter._tokens, tokens)

    def test_rate_overflow_discards_uncertain_pending_intervals(self):
        self.adapter._tokens = 0
        with patch('siegfried.daemon.focus.time.monotonic', return_value=self.adapter._token_time):
            self.assertFalse(self.adapter._accept_message('firefox', ':1.16'))
        self.assertEqual(self.adapter._generation, 1)
        self.assertEqual(len(self.adapter._pending), 0)

    def test_queue_overflow_is_bounded(self):
        self.adapter._pending.extend([(0, 'code', 1, 1)] * self.adapter.MAX_PENDING)
        self.assertFalse(self.adapter._accept_message('firefox', ':1.16'))
        self.assertEqual(len(self.adapter._pending), 0)
        self.assertEqual(self.adapter._generation, 1)

    def test_queue_worker_uses_arrival_time_and_null_focus(self):
        vault = MagicMock()
        self.tracker.vault = vault
        self.adapter._pending.extend([(0, 'code', 10, 100), (0, 'firefox', 14, 104),
                                      (0, '', 21, 111)])
        self.adapter._running = False
        self.adapter._run_worker()
        events = [call.args[0] for call in vault.append.call_args_list]
        self.assertEqual([e.data['duracion'] for e in events], [4, 7])
        self.assertEqual([e.ts for e in events], [100, 104])
        self.assertIsNone(self.tracker.snapshot()['active_app'])

    def test_persistence_failure_is_contained_and_does_not_log_payload(self):
        self.tracker.vault = MagicMock()
        self.tracker.vault.append.side_effect = RuntimeError('PRIVATE-TITLE')
        self.adapter._pending.extend([(0, 'code', 1, 1), (0, 'firefox', 3, 3)])
        self.adapter._running = False
        with self.assertLogs('siegfried.daemon.focus', level='WARNING') as logs:
            self.adapter._run_worker()
        self.assertNotIn('PRIVATE-TITLE', str(logs.output))
        self.assertIsNone(self.tracker.snapshot()['active_app'])

    def test_disconnect_discards_interval_before_reconnect(self):
        self.tracker.on_window_changed('code', 1, 1)
        self.assertTrue(self.adapter.stop(discard=True))
        self.assertIsNone(self.tracker.snapshot()['active_app'])
        self.assertIsNone(self.tracker.on_window_changed('firefox', 500, 500))
        self.assertEqual(self.tracker.close_active_interval(510, 510).data['duracion'], 10)

    def test_owner_change_fails_closed(self):
        self.adapter._owner_changed('org.kde.KWin', ':1.16', ':1.17')
        self.assertFalse(self.adapter.is_running())
        self.assertFalse(self.adapter._accept_message('code', ':1.17'))

    def test_partial_start_resources_are_released_and_stop_is_idempotent(self):
        bus, obj, match = MagicMock(), MagicMock(), MagicMock()
        self.adapter._running = False
        self.adapter._bus, self.adapter._service_object = bus, obj
        self.adapter._owner_match, self.adapter._bus_name = match, object()
        self.assertTrue(self.adapter.stop())
        bus.release_name.assert_called_once_with(self.adapter.DBUS_SERVICE_NAME)
        bus.close.assert_called_once()
        obj.remove_from_connection.assert_called_once()
        match.remove.assert_called_once()
        self.assertTrue(self.adapter.stop())

    def test_surviving_worker_is_reported_unclean(self):
        worker = MagicMock()
        worker.is_alive.return_value = True
        self.adapter._worker = worker
        self.assertFalse(self.adapter.stop())
        self.adapter._worker = None

    def test_disabled_adapter_does_not_import_native_bindings(self):
        self.adapter._running = False
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith(('dbus', 'gi')):
                raise AssertionError('native import in disabled core')
            return original(name, *args, **kwargs)
        with patch.dict(os.environ, {'SIEGFRIED_ENABLE_KWIN': '0'}), patch('builtins.__import__', guarded):
            self.assertFalse(self.adapter.start())

    def test_enabled_adapter_without_native_bindings_degrades(self):
        self.adapter._running = False
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name.startswith(('dbus', 'gi')):
                raise ImportError('optional missing')
            return original(name, *args, **kwargs)
        with patch.dict(os.environ, {'SIEGFRIED_ENABLE_KWIN': '1', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/fake'}), patch('builtins.__import__', missing):
            self.assertFalse(self.adapter.start())
        self.assertFalse(self.adapter.is_running())

    def test_deferred_old_disconnect_does_not_stop_reconnected_adapter(self):
        old, current = object(), MagicMock()
        self.adapter._bus = current
        self.adapter._disconnected(old)
        self.assertTrue(self.adapter.is_running())
        self.adapter._disconnected(current)
        self.assertFalse(self.adapter.is_running())

    def test_historical_ipc_today_and_week_use_exhaustive_mode(self):
        import tempfile
        from pathlib import Path
        from siegfried.daemon.app import SiegfriedDaemon
        from siegfried.storage.paths import SiegfriedPaths
        from siegfried.storage.aggregator import HistoricalAggregator, AggregatedMetrics
        from siegfried.contracts.ipc import IPCCommand, IPCRequest, IPCStatus
        with tempfile.TemporaryDirectory() as temp:
            paths = SiegfriedPaths(base_dir=Path(temp), runtime_dir=Path(temp))
            daemon = SiegfriedDaemon(paths=paths)
            daemon._orchestrator = MagicMock()
            for window, method in [('today', 'aggregate_today'), ('week', 'aggregate_week')]:
                with patch.object(HistoricalAggregator, method, return_value=AggregatedMetrics()) as scan:
                    response = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.QUERY, {
                        'prompt': 'resumen', 'is_historical': True, 'time_window': window}))
                    self.assertEqual(response.status, IPCStatus.OK.value)
                    scan.assert_called_once_with(allow_early_exit=False)

    def test_remote_bus_is_rejected_without_loading_native_modules(self):
        self.adapter._running = False
        with patch.dict(os.environ, {'SIEGFRIED_ENABLE_KWIN': '1',
                                    'DBUS_SESSION_BUS_ADDRESS': 'tcp:host=example.invalid,port=1'}), \
             patch('builtins.__import__', side_effect=AssertionError('unexpected native import')):
            self.assertFalse(self.adapter.start())
