"""F5.2 deterministic/adversarial tests. Never lock, suspend or modify real HOME."""
import builtins
import importlib.util
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import tracemalloc
import unittest
from unittest.mock import MagicMock, patch

from siegfried.core.rest import ClockSample, RestStatus, estimate_rest
from siegfried.daemon.app import SiegfriedDaemon
from siegfried.daemon.focus import FocusDBusAdapter, FocusTracker
from siegfried.daemon.session import SessionController, SessionState
from siegfried.integrations.session import SessionDBusAdapter
from siegfried.contracts.events import EventType
from siegfried.contracts.ipc import IPCCommand, IPCRequest, IPCStatus
from siegfried.storage.initialization import ensure_user_runtime
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.vault import Vault


def point(mono=10.0, elapsed=0.0, boot='boot-a', wall=None):
    return ClockSample(1700000000.0 + elapsed if wall is None else wall,
                       mono, 100.0 + elapsed, boot)


def active_values(**changes):
    values = dict(suspended=False, shutting_down=False, kde_locked=False,
                  locked_hint=False, active=True)
    values.update(changes)
    return values


class SessionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self.temp.name) / 'vault.jsonl')
        self.tracker = FocusTracker(self.vault)
        self.controller = SessionController(self.tracker)
        self.controller.begin(point())
        self.controller.initialize(active_values(), point())

    def tearDown(self):
        self.temp.cleanup()

    def events(self):
        return list(self.vault.read_events())


class TestASessionSignals(SessionFixture):
    def test_suspend_resume_pair_and_duplicate_are_idempotent(self):
        self.controller.handle('suspended', True, point(20, 10))
        self.controller.handle('suspended', True, point(21, 11))
        self.assertEqual(self.controller.snapshot()['state'], 'SUSPENDED')
        self.controller.handle('suspended', False, point(22, 28810))
        result = self.controller.rest_estimate
        self.assertEqual(result.status, RestStatus.ESTIMATED)
        self.assertEqual(result.estimated_minutes, 455)
        self.controller.handle('suspended', False, point(23, 28811))
        self.assertIs(result, self.controller.rest_estimate)
        self.assertEqual(self.events(), [])  # No fake sleep/wake events.

    def test_resume_without_onset_is_insufficient(self):
        self.controller.begin(point())
        self.controller.handle('suspended', False, point(11, 1))
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INSUFFICIENT_DATA)
        self.assertEqual(self.controller.snapshot()['state'], 'UNKNOWN')

    def test_disconnection_invalidates_absence_evidence(self):
        self.controller.handle('suspended', True, point(20, 10))
        self.controller.handle('degraded', None, point(21, 11))
        self.controller.handle('suspended', False, point(22, 28810))
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INSUFFICIENT_DATA)
        self.assertEqual(self.controller.snapshot()['state'], 'DEGRADED')

    def test_out_of_order_signals_fail_closed(self):
        self.controller.handle('suspended', True, point(20, 10))
        self.assertFalse(self.controller.handle('suspended', False, point(19, 9)))
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INVALID_INTERVAL)
        self.assertFalse(self.tracker.session_gate[0])

    def test_shutdown_is_distinct_from_suspend(self):
        self.controller.handle('shutting_down', True, point(20, 10))
        self.assertEqual(self.controller.snapshot()['state'], 'SHUTTING_DOWN')
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INSUFFICIENT_DATA)
        self.controller.handle('shutting_down', False, point(21, 11))
        self.assertEqual(self.controller.snapshot()['state'], 'ACTIVE')

    def test_session_loss_remains_closed_until_restart(self):
        self.controller.handle('session_lost', None, point(20, 10))
        self.controller.handle('active', True, point(21, 11))
        self.assertEqual(self.controller.snapshot()['state'], 'SESSION_LOST')
        self.assertFalse(self.tracker.session_gate[0])

    def test_startup_during_sleep_has_no_observed_onset(self):
        self.controller.begin(point())
        self.controller.initialize(active_values(suspended=True), point())
        self.controller.handle('suspended', False, point(20, 28800))
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INSUFFICIENT_DATA)

    def test_partial_snapshot_is_unknown_not_active(self):
        values = active_values()
        values['active'] = None
        self.controller.initialize(values, point())
        self.assertEqual(self.controller.snapshot()['state'], 'UNKNOWN')
        self.assertTrue(self.controller.snapshot()['observability_degraded'])

    def test_inactive_session_is_not_sleep(self):
        self.controller.handle('active', False, point(20, 10))
        self.assertEqual(self.controller.snapshot()['state'], 'INACTIVE')
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INSUFFICIENT_DATA)

    def test_boot_identity_change_never_compares_old_monotonic_interval(self):
        self.controller.handle('suspended', True, point(20,10))
        self.assertFalse(self.controller.handle('suspended', False, point(1,28800,boot='boot-b')))
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INVALID_INTERVAL)
        self.assertFalse(self.tracker.session_gate[0])


class TestBFocusCoordination(SessionFixture):
    def test_suspend_closes_active_interval_at_signal_arrival(self):
        self.tracker.on_window_changed('code', 11, 1700000001)
        self.controller.handle('suspended', True, point(21, 11))
        self.assertEqual(self.events()[0].data['duracion'], 10)
        self.tracker.on_window_changed('firefox', 22, 1700000002)
        self.assertIsNone(self.tracker.snapshot()['active_app'])

    def test_lock_duplicates_close_once_and_do_not_change_user_preference(self):
        self.tracker.on_window_changed('code', 11, 1700000001)
        self.controller.handle('kde_locked', True, point(21, 11))
        self.controller.handle('kde_locked', True, point(22, 12))
        self.assertEqual(len(self.events()), 1)
        self.assertTrue(self.tracker.snapshot()['is_active'])
        self.assertFalse(self.tracker.snapshot()['session_allowed'])

    def test_unlock_requires_new_focus_not_previous_interval(self):
        self.tracker.on_window_changed('code', 11, 1700000001)
        self.controller.handle('kde_locked', True, point(21, 11))
        self.controller.handle('kde_locked', False, point(121, 111))
        self.assertIsNone(self.tracker.snapshot()['active_app'])
        self.tracker.on_window_changed('code', 122, 1700000112)
        self.tracker.close_active_interval(132, 1700000122)
        self.assertEqual([e.data['duracion'] for e in self.events()], [10, 10])

    def test_both_lock_sources_must_clear(self):
        self.controller.handle('kde_locked', True, point(20, 10))
        self.controller.handle('locked_hint', True, point(21, 11))
        self.controller.handle('kde_locked', False, point(22, 12))
        self.assertFalse(self.tracker.session_gate[0])
        self.controller.handle('locked_hint', False, point(23, 13))
        self.assertTrue(self.tracker.session_gate[0])

    def test_manual_disable_survives_unlock(self):
        self.tracker.set_active(False)
        self.controller.handle('kde_locked', True, point(20, 10))
        self.controller.handle('kde_locked', False, point(21, 11))
        self.tracker.on_window_changed('code', 22, 1700000012)
        self.assertIsNone(self.tracker.snapshot()['active_app'])
        self.assertFalse(self.tracker.snapshot()['is_active'])

    def test_stale_queued_focus_is_rejected_after_resume(self):
        epoch = self.tracker.session_gate[1]
        self.controller.handle('suspended', True, point(20, 10))
        self.controller.handle('suspended', False, point(21, 28810))
        self.tracker.on_window_changed('code', 19, 1700000009, epoch=epoch)
        self.assertIsNone(self.tracker.snapshot()['active_app'])
        self.tracker.on_window_changed('code', 22, 1700028811)
        self.assertEqual(self.tracker.snapshot()['active_app'], 'code')

    def test_admission_barrier_does_not_wait_for_vault_lock(self):
        self.tracker._lock.acquire()
        done = threading.Event()
        thread = threading.Thread(target=lambda: (self.tracker.pause_session_input(), done.set()))
        try:
            thread.start()
            self.assertTrue(done.wait(.5))
        finally:
            self.tracker._lock.release()
            thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_older_enable_cannot_override_newer_queued_deny(self):
        first = self.tracker.pause_session_input()
        second = self.tracker.pause_session_input()
        self.tracker.set_session_allowed(True, 20, 1700000010, gate_ticket=first)
        self.assertFalse(self.tracker.session_gate[0])
        self.tracker.set_session_allowed(True, 21, 1700000011, gate_ticket=second)
        self.assertTrue(self.tracker.session_gate[0])

    def test_tracker_reset_does_not_override_session_gate(self):
        self.controller.handle('kde_locked', True, point(20, 10))
        self.tracker.on_resume()
        self.tracker.on_window_changed('code', 21, 1700000011)
        self.assertIsNone(self.tracker.snapshot()['active_app'])

    def test_stop_and_restart_close_once_and_forget_absence(self):
        self.tracker.on_window_changed('code', 11, 1700000001)
        self.controller.stop(point(21, 11))
        self.controller.stop(point(22, 12))
        self.assertEqual(len(self.events()), 1)
        self.controller.begin(point(23, 13))
        self.controller.initialize(active_values(), point(23, 13))
        self.assertIsNone(self.tracker.snapshot()['active_app'])
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INSUFFICIENT_DATA)

    def test_concurrent_enable_suspend_focus_and_shutdown_have_no_deadlock(self):
        tracker = FocusTracker()
        controller = SessionController(tracker)
        def sample():
            return ClockSample(time.time(), time.monotonic(), time.monotonic(), 'a')
        controller.begin(sample())
        controller.initialize(active_values(), sample())
        barrier = threading.Barrier(4)
        failures = []
        def worker(kind):
            try:
                barrier.wait(1)
                for i in range(200):
                    if kind == 'focus':
                        tracker.on_window_changed('code' if i % 2 else 'firefox')
                    elif kind == 'enable':
                        tracker.set_active(bool(i % 2))
                    else:
                        controller.handle('suspended', bool(i % 2), sample())
                if kind == 'focus':
                    controller.stop(sample())
            except BaseException as ex:
                failures.append(ex)
        threads = [threading.Thread(target=worker, args=(kind,)) for kind in ('focus','enable','session')]
        for thread in threads:
            thread.start()
        barrier.wait(1)
        for thread in threads:
            thread.join(4)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_adapter_duplicate_cache_recovers_same_app_in_new_epoch(self):
        adapter = FocusDBusAdapter(self.tracker)
        adapter._kwin_owner, adapter._running = ':1.16', True
        self.assertTrue(adapter._accept_message('code', ':1.16'))
        adapter._running = False
        adapter._run_worker()
        def now():
            return ClockSample(time.time(), time.monotonic(), time.monotonic(), 'boot-a')
        self.controller.handle('kde_locked', True, now())
        self.controller.handle('kde_locked', False, now())
        adapter._running = True
        self.assertTrue(adapter._accept_message('code', ':1.16'))
        self.assertEqual(len(adapter._pending), 1)
        adapter._running = False
        adapter._run_worker()
        self.assertEqual(self.tracker.snapshot()['active_app'], 'code')


class TestCEstimatedRest(unittest.TestCase):
    def estimate(self, seconds, **kwargs):
        return estimate_rest(point(), point(12, seconds), cause='suspend', continuous_evidence=True, **kwargs)

    def test_eight_hours_estimates_seven_hours_35_minutes(self):
        result = self.estimate(8*3600)
        self.assertEqual(result.status, RestStatus.ESTIMATED)
        self.assertEqual(result.estimated_minutes, 455)
        self.assertIn('7 h 35 min', result.describe())
        self.assertNotIn('Dormiste', result.describe())

    def test_brief_suspend_below_90_minutes_is_not_applicable(self):
        self.assertEqual(self.estimate(5399).status, RestStatus.NOT_APPLICABLE)

    def test_exact_90_minutes_is_estimated(self):
        self.assertEqual(self.estimate(5400).estimated_minutes, 65)

    def test_restart_without_verified_wall_continuity_is_insufficient(self):
        result = estimate_rest(point(90000), point(1, 28800, boot='boot-b'),
                               cause='shutdown', continuous_evidence=True)
        self.assertEqual(result.status, RestStatus.INSUFFICIENT_DATA)

    def test_verified_cross_boot_pair_uses_wall_not_monotonic(self):
        result = estimate_rest(point(90000), point(1, 28800, boot='boot-b'),
                               cause='shutdown', continuous_evidence=True, wall_clock_verified=True)
        self.assertEqual(result.estimated_minutes, 455)

    def test_wall_jump_vs_boottime_is_invalid(self):
        end = ClockSample(1700032400, 12, 28900, 'boot-a')
        self.assertEqual(estimate_rest(point(), end, cause='suspend', continuous_evidence=True).status,
                         RestStatus.INVALID_INTERVAL)

    def test_backwards_wall_is_invalid(self):
        self.assertEqual(estimate_rest(point(), point(12, 28800, wall=1600000000), cause='suspend',
                                       continuous_evidence=True).status, RestStatus.INVALID_INTERVAL)

    def test_monotonic_alone_cannot_measure_suspend(self):
        start = ClockSample(1700000000, 10, None, 'a')
        end = ClockSample(1700028800, 12, None, 'a')
        self.assertEqual(estimate_rest(start, end, cause='suspend', continuous_evidence=True).status,
                         RestStatus.INSUFFICIENT_DATA)

    def test_reversed_boottime_is_invalid(self):
        end = ClockSample(1700028800, 12, 90, 'boot-a')
        self.assertEqual(estimate_rest(point(), end, cause='suspend', continuous_evidence=True).status,
                         RestStatus.INVALID_INTERVAL)

    def test_reversed_same_boot_monotonic_is_invalid(self):
        self.assertEqual(estimate_rest(point(), point(9,28800), cause='suspend',
                                       continuous_evidence=True).status, RestStatus.INVALID_INTERVAL)

    def test_missing_data_is_insufficient(self):
        self.assertEqual(estimate_rest(None, point(), cause='suspend', continuous_evidence=True).status,
                         RestStatus.INSUFFICIENT_DATA)

    def test_incomplete_pair_is_insufficient(self):
        self.assertEqual(estimate_rest(point(), point(12,28800), cause='suspend',
                                       continuous_evidence=False).status, RestStatus.INSUFFICIENT_DATA)

    def test_lock_is_not_a_rest_absence(self):
        self.assertEqual(estimate_rest(point(), point(12,28800), cause='lock',
                                       continuous_evidence=True).status, RestStatus.INSUFFICIENT_DATA)

    def test_nonfinite_malformed_and_boolean_timestamps_are_invalid(self):
        for value in (math.nan, math.inf, -1, True, 'private title'):
            with self.subTest(value=value):
                self.assertEqual(estimate_rest(ClockSample(value), point(), cause='suspend',
                                               continuous_evidence=True).status, RestStatus.INVALID_INTERVAL)

    def test_boot_hook_bridge_has_no_desktop_effects_or_assumed_evidence(self):
        spec = importlib.util.spec_from_file_location('boot_hook_f52', Path('scripts/boot_hook.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch('subprocess.Popen') as desktop:
            result = module.calculate_estimated_rest_window(1700000000, 1700028800)
            self.assertEqual(result.status, RestStatus.INSUFFICIENT_DATA)
            verified = module.calculate_estimated_rest_window(1700000000, 1700028800, interval_verified=True)
            self.assertEqual(verified.estimated_minutes, 455)
            desktop.assert_not_called()


class TestDSecurityPrivacy(SessionFixture):
    def adapter(self):
        adapter = SessionDBusAdapter(self.controller, clock=lambda: point(20,10))
        adapter._running = True
        adapter._owners = {'logind': ':1.8', 'kde': ':1.16'}
        return adapter

    def test_other_connection_cannot_spoof_session_state(self):
        adapter = self.adapter()
        self.assertFalse(adapter._signal('kde', 'kde_locked', True, ':1.999'))
        self.assertFalse(adapter._signal('logind', 'suspended', True, None))
        self.assertEqual(len(adapter._pending), 0)
        self.assertTrue(self.tracker.session_gate[0])

    def test_malformed_booleans_are_rejected_without_logging_payload(self):
        adapter = self.adapter()
        for value in ('http://secret/title', 1, None, [], {'title':'secret'}):
            self.assertFalse(adapter._signal('kde', 'kde_locked', value, ':1.16'))
        self.assertTrue(self.tracker.session_gate[0])

    def test_properties_payload_limits_and_scope(self):
        adapter = self.adapter()
        self.assertFalse(adapter._properties('wrong', {'Active': False}, [], ':1.8'))
        self.assertFalse(adapter._properties('org.freedesktop.login1.Session', {'x'+str(i):i for i in range(33)}, [], ':1.8'))
        self.assertFalse(adapter._properties('org.freedesktop.login1.Session', {'Active':'private'}, [], ':1.8'))
        self.assertEqual(len(adapter._pending), 0)

    def test_property_invalidation_becomes_unknown_evidence(self):
        adapter = self.adapter()
        adapter._properties('org.freedesktop.login1.Session', {}, ['Active'], ':1.8')
        adapter._running = False
        adapter._run_worker()
        self.assertEqual(self.controller.snapshot()['state'], 'DEGRADED')

    def test_only_target_session_removal_is_processed(self):
        adapter = self.adapter()
        adapter._session_id, adapter._session_path = '3', '/org/freedesktop/login1/session/_33'
        adapter._removed('4', '/elsewhere', ':1.8')
        self.assertEqual(len(adapter._pending), 0)
        adapter._removed('3', adapter._session_path, ':1.8')
        adapter._running = False
        adapter._run_worker()
        self.assertEqual(self.controller.snapshot()['state'], 'SESSION_LOST')

    def test_no_sensitive_fields_no_cloud_and_secure_vault_permissions(self):
        with patch('urllib.request.urlopen') as cloud, patch('subprocess.Popen') as processes:
            self.tracker.on_window_changed('https://private/title', 11, 1700000001)
            self.controller.handle('kde_locked', True, point(20,10))
            cloud.assert_not_called()
            processes.assert_not_called()
        event = self.events()[0]
        self.assertEqual(event.type, EventType.WINDOW_FOCUS_SAMPLED.value)
        self.assertEqual(set(event.data), {'aplicacion', 'categoria', 'duracion'})
        self.assertNotIn('private', str(event.to_dict()))
        self.assertEqual(self.vault.vault_path.stat().st_mode & 0o777, 0o600)

    def test_session_events_do_not_misuse_circadian_catalog(self):
        self.controller.handle('suspended', True, point(20,10))
        self.controller.handle('suspended', False, point(21,28810))
        self.assertEqual(self.events(), [])

    def test_controller_rejects_extra_fields_and_unknown_kind(self):
        self.assertFalse(self.controller.initialize({'title':'private'}, point()))
        self.assertFalse(self.controller.handle('execute', True, point()))
        self.assertFalse(self.controller.handle('suspended', 'true', point()))


class TestEResilience(SessionFixture):
    def make_fake_adapter(self):
        adapter = SessionDBusAdapter(self.controller, clock=lambda: point(20,10))
        adapter._running = True
        adapter._owners = {'kde': ':1.16', 'logind': ':1.8'}
        return adapter

    def test_disabled_adapter_imports_no_native_packages(self):
        adapter = SessionDBusAdapter(self.controller)
        with patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'0'}), patch('builtins.__import__', side_effect=AssertionError('unexpected import')):
            self.assertFalse(adapter.start())

    def test_missing_dbus_or_gi_degrades_without_core_failure(self):
        original = builtins.__import__
        for missing in ('dbus', 'gi'):
            adapter = SessionDBusAdapter(self.controller)
            def unavailable(name, *args, **kwargs):
                if name == missing or name.startswith(missing+'.'):
                    raise ImportError('optional binding absent')
                return original(name, *args, **kwargs)
            with patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'1'}), patch('builtins.__import__', unavailable):
                self.assertFalse(adapter.start())
            self.assertTrue(adapter.stop())

    def test_queue_backpressure_and_memory_remain_bounded(self):
        adapter = self.make_fake_adapter()
        tracemalloc.start()
        baseline, _ = tracemalloc.get_traced_memory()
        try:
            for i in range(10000):
                adapter._signal('kde', 'kde_locked', bool(i % 2), ':1.16')
                self.assertLessEqual(len(adapter._pending), adapter.MAX_PENDING)
            current, peak = tracemalloc.get_traced_memory()
            self.assertLess(current-baseline, 200000)
            self.assertLess(peak-baseline, 1000000)
        finally:
            tracemalloc.stop()
        self.assertGreater(adapter.dropped, 0)
        adapter._running = False
        adapter._run_worker()
        self.assertEqual(self.controller.snapshot()['state'], 'DEGRADED')
        self.assertEqual(self.controller.rest_estimate.status, RestStatus.INSUFFICIENT_DATA)

    def test_duplicate_pending_signals_preserve_first_boundary(self):
        adapter = self.make_fake_adapter()
        adapter._enqueue('suspended', True, point(20,10))
        adapter._enqueue('suspended', True, point(21,11))
        self.assertEqual(len(adapter._pending), 1)
        self.assertEqual(adapter._pending[0][2].monotonic, 20)

    def test_deferred_old_disconnection_does_not_degrade_restart(self):
        adapter = self.make_fake_adapter()
        current = MagicMock()
        adapter._buses = {'kde':current}
        adapter._disconnected(object())
        self.assertEqual(len(adapter._pending), 0)
        adapter._disconnected(current)
        self.assertEqual(adapter._pending[0][0], 'degraded')

    def test_stop_cancels_matches_connections_and_is_idempotent(self):
        adapter = self.make_fake_adapter()
        bus, match = MagicMock(), MagicMock()
        adapter._buses = {'kde':bus}
        adapter._matches = [match]
        self.assertTrue(adapter.stop())
        match.remove.assert_called_once()
        bus.close.assert_called_once()
        self.assertTrue(adapter.stop())
        self.assertFalse(adapter.is_running())

    def test_slow_storage_does_not_hold_bus_queue_or_block_shutdown_indefinitely(self):
        entered, release = threading.Event(), threading.Event()
        self.tracker.on_window_changed('code', 11, 1700000001)
        def slow(event):
            entered.set()
            self.assertTrue(release.wait(3))
        self.tracker.vault = MagicMock()
        self.tracker.vault.append.side_effect = slow
        adapter = self.make_fake_adapter()
        adapter._worker = threading.Thread(target=adapter._run_worker)
        adapter._worker.start()
        try:
            adapter._signal('kde', 'kde_locked', True, ':1.16')
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            self.assertTrue(adapter._signal('logind', 'suspended', True, ':1.8'))
            self.assertLess(time.monotonic()-start, .2)
            start = time.monotonic()
            self.assertFalse(adapter.stop(timeout=.02))
            self.assertLess(time.monotonic()-start, .2)
        finally:
            release.set()
            adapter._worker.join(3)
        self.assertFalse(adapter._worker.is_alive())

    def test_daemon_timers_and_posture_remain_independent_without_kde_logind(self):
        paths = SiegfriedPaths(base_dir=Path(self.temp.name)/'base', runtime_dir=Path(self.temp.name)/'run')
        ensure_user_runtime(paths)
        notifier, audio = MagicMock(), MagicMock()
        daemon = SiegfriedDaemon(paths, notifier=notifier, audio_player=audio)
        with patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'0', 'SIEGFRIED_ENABLE_KWIN':'0'}):
            daemon.start()
        try:
            response = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.START_FOCUS, {'duration_min':25,'task':'isolated'}))
            self.assertEqual(response.status, IPCStatus.OK.value)
            remaining = daemon.focus_timer.remaining_seconds()
            daemon.session_controller.begin(point())
            daemon.session_controller.initialize(active_values(), point())
            daemon.session_controller.handle('suspended', True, point(20,10))
            self.assertTrue(daemon.focus_timer.is_active())
            self.assertAlmostEqual(remaining, daemon.focus_timer.remaining_seconds(), delta=.1)
            daemon.state_machine.add_sitting_time(3001)
            with patch.object(daemon.alert_coordinator, 'trigger_posture_warning') as warning:
                daemon._check_posture_milestones()
                warning.assert_called_once()
            ping = daemon.handle_ipc_request(IPCRequest.create(IPCCommand.PING))
            self.assertEqual(ping.status, IPCStatus.OK.value)
        finally:
            self.assertTrue(daemon.stop())

    def test_daemon_remains_operational_with_missing_optional_bindings(self):
        paths = SiegfriedPaths(base_dir=Path(self.temp.name)/'base', runtime_dir=Path(self.temp.name)/'run')
        ensure_user_runtime(paths)
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name.startswith(('dbus','gi')):
                raise ImportError('optional absent')
            return original(name, *args, **kwargs)
        daemon = SiegfriedDaemon(paths, notifier=MagicMock(), audio_player=MagicMock())
        with patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'1','SIEGFRIED_ENABLE_KWIN':'1'}), patch('builtins.__import__', missing):
            daemon.start()
            try:
                self.assertEqual(daemon.handle_ipc_request(IPCRequest.create(IPCCommand.PING)).status, IPCStatus.OK.value)
                self.assertFalse(daemon.session_dbus_adapter.is_running())
                self.assertFalse(daemon.focus_dbus_adapter.is_running())
            finally:
                self.assertTrue(daemon.stop())

    def test_persistence_exception_is_contained_without_private_exception_text(self):
        adapter = self.make_fake_adapter()
        self.tracker.on_window_changed('code', 11, 1700000001)
        self.tracker.vault = MagicMock()
        self.tracker.vault.append.side_effect = RuntimeError('PRIVATE-URL-AND-TITLE')
        adapter._enqueue('kde_locked', True)
        adapter._running = False
        with self.assertLogs('siegfried.integrations.session', level='WARNING') as logs:
            adapter._run_worker()
        self.assertNotIn('PRIVATE-URL-AND-TITLE', str(logs.output))
        self.assertEqual(self.controller.snapshot()['state'], 'DEGRADED')
        self.assertFalse(self.tracker.session_gate[0])

        # A terminal Vault error must also be contained and reported as failure.
        self.controller.begin(point())
        self.controller.initialize(active_values(), point())
        self.tracker.on_window_changed('code', 11, 1700000001)
        terminal = self.make_fake_adapter()
        terminal._worker = threading.Thread(target=terminal._run_worker)
        terminal._worker.start()
        with self.assertLogs('siegfried.integrations.session', level='WARNING') as logs:
            self.assertFalse(terminal.stop())
        self.assertNotIn('PRIVATE-URL-AND-TITLE', str(logs.output))
        self.assertFalse(terminal._worker.is_alive())
        self.assertFalse(terminal.stop())
        self.assertFalse(self.tracker.session_gate[0])

    def test_daemon_shutdown_reports_residual_worker_without_tracker_lock_wait(self):
        entered, release = threading.Event(), threading.Event()
        paths = SiegfriedPaths(base_dir=Path(self.temp.name)/'base', runtime_dir=Path(self.temp.name)/'run')
        ensure_user_runtime(paths)
        daemon = SiegfriedDaemon(paths, notifier=MagicMock(), audio_player=MagicMock())
        daemon.session_controller.begin(point())
        daemon.session_controller.initialize(active_values(), point())
        daemon.focus_tracker.on_window_changed('code', 11, 1700000001)
        def slow(event):
            entered.set()
            release.wait(3)
        daemon.focus_tracker.vault = MagicMock()
        daemon.focus_tracker.vault.append.side_effect = slow
        adapter = daemon.session_dbus_adapter
        adapter.clock = lambda: point(20,10)
        adapter._running = True
        adapter._owners = {'kde':':1.16'}
        adapter._worker = threading.Thread(target=adapter._run_worker)
        adapter._worker.start()
        try:
            adapter._signal('kde','kde_locked', True, ':1.16')
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            self.assertFalse(daemon.stop(timeout_seconds=.08))
            self.assertLess(time.monotonic()-start, .3)
            self.assertFalse(daemon.stop(timeout_seconds=.08))
        finally:
            release.set()
            adapter._worker.join(3)
        self.assertFalse(adapter._worker.is_alive())


class TestFNativeStartupFixtures(unittest.TestCase):
    """Simulated native API boundaries, no host bus access from these tests."""
    def fixture(self, unavailable=None, initial_state="active"):
        import sys
        import types
        class Loop:
            def __init__(self):
                self.done = threading.Event()
            def run(self):
                self.done.wait(3)
            def quit(self):
                self.done.set()
        class Bus:
            def __init__(self, source):
                self.source, self.closed = source, False
            def set_exit_on_disconnect(self, value): pass
            def call_on_disconnection(self, callback): self.disconnect = callback
            def close(self): self.closed = True
            def add_signal_receiver(self, *args, **kwargs): return MagicMock()
            def call_blocking(self, name, path, interface, member, signature, args, timeout):
                if member == 'GetNameOwner': return ':1.8' if self.source == 'logind' else ':1.16'
                if member == 'GetConnectionUnixUser': return 0 if self.source == 'logind' else os.getuid()
                if member in ('GetSession', 'GetSessionByPID'): return '/org/freedesktop/login1/session/_33'
                if member == 'GetActive': return False
                if member == 'Get':
                    return {'PreparingForSleep': False, 'PreparingForShutdown': False,
                            'User': (os.getuid(), '/user'), 'Type':'wayland', 'Class':'user',
                            'Id':'3', 'Active':True, 'LockedHint':False, 'State':initial_state}[args[1]]
                raise AssertionError(member)
        buses = []
        def factory(source):
            def create(**kwargs):
                if source == unavailable or (isinstance(unavailable, tuple) and source in unavailable):
                    raise RuntimeError('source unavailable')
                bus = Bus(source)
                buses.append(bus)
                return bus
            return create
        dbus = types.ModuleType('dbus')
        dbus.Boolean, dbus.UInt32 = bool, int
        dbus.DBusException = RuntimeError
        dbus.SystemBus, dbus.SessionBus = factory('logind'), factory('kde')
        dbus.bus = types.SimpleNamespace(BusConnection=lambda address, **kwargs: factory('kde')(**kwargs))
        glib = types.ModuleType('dbus.mainloop.glib')
        glib.DBusGMainLoop, glib.threads_init = lambda: None, lambda: None
        gi_repo = types.ModuleType('gi.repository')
        gi_repo.GLib = types.SimpleNamespace(MainLoop=Loop, idle_add=lambda callback: callback())
        modules = {'dbus':dbus, 'dbus.mainloop':types.ModuleType('dbus.mainloop'),
                   'dbus.mainloop.glib':glib, 'gi':types.ModuleType('gi'), 'gi.repository':gi_repo}
        return patch.dict(sys.modules, modules), buses

    def check_start(self, unavailable=None):
        modules, buses = self.fixture(unavailable)
        controller = SessionController(FocusTracker())
        adapter = SessionDBusAdapter(controller, clock=lambda: point())
        with modules, patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'1',
                                             'DBUS_SESSION_BUS_ADDRESS':'unix:path=/isolated'}):
            try:
                self.assertTrue(adapter.start())
                self.assertTrue(adapter.wait_idle())
                self.assertEqual(controller.snapshot()['state'], 'ACTIVE' if unavailable is None else 'UNKNOWN')
                if unavailable:
                    self.assertFalse(adapter.sources[unavailable])
            finally:
                self.assertTrue(adapter.stop())
            self.assertTrue(all(bus.closed for bus in buses))

    def test_without_kde_still_subscribes_logind_and_keeps_unknown_focus(self):
        self.check_start('kde')

    def test_without_logind_still_subscribes_kde_and_keeps_unknown_focus(self):
        self.check_start('logind')

    def test_both_sources_start_and_stop_cleanly(self):
        self.check_start()

    def test_nonlocal_bus_addresses_are_not_connected(self):
        modules, buses = self.fixture()
        adapter = SessionDBusAdapter(SessionController(FocusTracker()), clock=lambda: point())
        with modules, patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'1',
                  'DBUS_SESSION_BUS_ADDRESS':'tcp:host=example.invalid,port=1',
                  'DBUS_SYSTEM_BUS_ADDRESS':'unix:path=/tmp/a;tcp:host=example.invalid'}):
            self.assertFalse(adapter.start())
            self.assertTrue(adapter.stop())
        self.assertEqual(buses, [])

    def test_extreme_numeric_input_is_a_status_not_an_exception(self):
        result = estimate_rest(ClockSample(10**10000), point(), cause='suspend', continuous_evidence=True)
        self.assertEqual(result.status, RestStatus.INVALID_INTERVAL)

    def test_malformed_evidence_flags_cannot_attest_an_interval(self):
        result = estimate_rest(point(), point(12,28800), cause='suspend', continuous_evidence='true')
        self.assertEqual(result.status, RestStatus.INVALID_INTERVAL)

    def test_startup_closing_session_is_lost_not_merely_inactive(self):
        modules, buses = self.fixture(initial_state='closing')
        adapter = SessionDBusAdapter(SessionController(FocusTracker()), clock=lambda: point())
        with modules, patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'1',
                                             'DBUS_SESSION_BUS_ADDRESS':'unix:path=/isolated'}):
            try:
                self.assertTrue(adapter.start())
                self.assertTrue(adapter.wait_idle())
                self.assertEqual(adapter.controller.snapshot()['state'], 'SESSION_LOST')
            finally:
                self.assertTrue(adapter.stop())

    def test_both_buses_unavailable_is_controlled_degradation(self):
        modules, buses = self.fixture(unavailable=('logind','kde'))
        adapter = SessionDBusAdapter(SessionController(FocusTracker()), clock=lambda: point())
        with modules, patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'1',
                                             'DBUS_SESSION_BUS_ADDRESS':'unix:path=/isolated'}):
            self.assertFalse(adapter.start())
            self.assertTrue(adapter.stop())
        self.assertEqual(buses, [])
        self.assertFalse(adapter.is_running())


if __name__ == '__main__':
    unittest.main()
