#!/usr/bin/env python3
"""Read-only host subscriptions + synthetic events on an owned isolated bus.

Never suspends, locks, writes real HOME, configures services, or modifies KWin.
Synthetic clock offsets exist only in this process. Native bindings are optional
for production; this explicit verification tool needs those already installed.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from siegfried.core.rest import ClockSample, RestStatus
from siegfried.daemon.focus import FocusDBusAdapter, FocusTracker
from siegfried.daemon.session import SessionController
from siegfried.integrations.session import SessionClock, SessionDBusAdapter, MANAGER, MANAGER_PATH, SAVER, SAVER_PATH
from siegfried.storage.vault import Vault


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('verification synchronization timeout')


def host_probe():
    c = SessionController(FocusTracker())
    a = SessionDBusAdapter(c)
    result = []
    with patch.dict(os.environ, {'SIEGFRIED_ENABLE_SESSION':'1'}):
        for _ in range(3):
            buses = []
            try:
                assert a.start(), 'host subscriptions unavailable'
                assert a.wait_idle()
                buses = list(a._buses.values())
                result.append({'sources':dict(a.sources), 'state':c.snapshot(),
                               'subscriptions':len(a._matches),
                               'session_path':a._session_path})
            finally:
                assert a.stop(), 'host adapter cleanup incomplete'
                assert not a._matches and not a._buses and not a._owners
                assert all(not bus.get_is_connected() for bus in buses)
                assert not any(t and t.is_alive() for t in (a._thread,a._worker))
    return {'cycles':result, 'connections_and_threads_released':True,
            'physical_suspend':'PENDING; not performed', 'physical_lock':'PENDING; not performed'}


def isolated_probe():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    process = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    connections = []
    session_adapter = focus_adapter = None
    producer_name = None
    summary = {}
    try:
        address = process.stdout.readline().strip()
        assert address.startswith('unix:'), 'isolated bus did not start'
        producer, receiver, attacker = [dbus.bus.BusConnection(address, mainloop=DBusGMainLoop()) for _ in range(3)]
        connections.extend((producer,receiver,attacker))
        # This name is claimed exclusively on the test bus, never on the host bus.
        producer_name = dbus.service.BusName('org.kde.KWin', producer, do_not_queue=True)

        class SleepSource(dbus.service.Object):
            @dbus.service.signal(MANAGER, signature='b')
            def PrepareForSleep(self, value): pass
        class LockSource(dbus.service.Object):
            @dbus.service.signal(SAVER, signature='b')
            def ActiveChanged(self, value): pass
        sleeper = SleepSource(producer, MANAGER_PATH)
        locker = LockSource(producer, SAVER_PATH)
        with tempfile.TemporaryDirectory(prefix='siegfried_f52_') as temp:
            vault = Vault(Path(temp)/'vault.jsonl')
            tracker = FocusTracker(vault)
            controller = SessionController(tracker)
            clock = SessionClock()
            offset = [0.0]
            def synthetic_clock():
                sample = clock()
                return ClockSample(sample.wall+offset[0], sample.monotonic,
                                   sample.boottime+offset[0], sample.boot_id)
            controller.begin(synthetic_clock())
            controller.initialize(dict(suspended=False, shutting_down=False, kde_locked=False,
                                       locked_hint=False, active=True), synthetic_clock())
            session_adapter = SessionDBusAdapter(controller, clock=synthetic_clock)
            session_adapter._running = True
            session_adapter._native_bool = dbus.Boolean
            session_adapter._buses = {'logind':receiver, 'kde':receiver}
            session_adapter._owners = {'logind':producer.get_unique_name(), 'kde':producer.get_unique_name()}
            session_adapter._loop = GLib.MainLoop()
            session_adapter._subscribe(receiver, 'logind', MANAGER, MANAGER_PATH, 'PrepareForSleep',
                lambda value, sender=None: session_adapter._signal('logind','suspended',value,sender))
            session_adapter._subscribe(receiver, 'kde', SAVER, SAVER_PATH, 'ActiveChanged',
                lambda value, sender=None: session_adapter._signal('kde','kde_locked',value,sender))
            session_adapter._worker = threading.Thread(target=session_adapter._run_worker, name='SiegfriedSessionWorker', daemon=True)
            session_adapter._thread = threading.Thread(target=session_adapter._run_loop, name='SiegfriedSessionDBus', daemon=True)
            ready = threading.Event()
            GLib.idle_add(lambda: ready.set() or False)
            session_adapter._worker.start()
            session_adapter._thread.start()
            assert ready.wait(1)
            focus_adapter = FocusDBusAdapter(tracker)
            with patch.dict(os.environ, {'SIEGFRIED_ENABLE_KWIN':'1', 'DBUS_SESSION_BUS_ADDRESS':address}):
                assert focus_adapter.start(mainloop_owner=session_adapter)
            focus_connections = [focus_adapter._bus]
            focus = dbus.Interface(producer.get_object(focus_adapter.DBUS_SERVICE_NAME, '/FocusWatcher'), focus_adapter.DBUS_INTERFACE)
            fake_focus = dbus.Interface(attacker.get_object(focus_adapter.DBUS_SERVICE_NAME, '/FocusWatcher'), focus_adapter.DBUS_INTERFACE)
            assert focus.WindowChanged('code')
            wait_for(lambda: tracker.snapshot()['active_app'] == 'code')
            time.sleep(.03)
            locker.ActiveChanged(True)
            wait_for(lambda: controller.snapshot()['state'] == 'LOCKED')
            assert not focus.WindowChanged('firefox')
            count = len(list(vault.read_events()))
            locker.ActiveChanged(True)
            time.sleep(.03)
            assert len(list(vault.read_events())) == count
            locker.ActiveChanged(False)
            wait_for(lambda: controller.snapshot()['state'] == 'ACTIVE')
            assert tracker.snapshot()['active_app'] is None
            # Same application as before lock must recover, not be stale-deduplicated.
            assert focus.WindowChanged('code')
            wait_for(lambda: tracker.snapshot()['active_app'] == 'code')
            time.sleep(.03)
            sleeper.PrepareForSleep(True)
            wait_for(lambda: controller.snapshot()['state'] == 'SUSPENDED')
            assert tracker.snapshot()['active_app'] is None
            offset[0] = 28800
            sleeper.PrepareForSleep(False)
            wait_for(lambda: controller.snapshot()['state'] == 'ACTIVE')
            assert controller.rest_estimate.status == RestStatus.ESTIMATED
            assert abs(controller.rest_estimate.estimated_minutes-455) < .05
            summary['synthetic_8h_estimated_minutes'] = controller.rest_estimate.estimated_minutes
            assert not fake_focus.WindowChanged('firefox')
            forged = dbus.lowlevel.SignalMessage(MANAGER_PATH, MANAGER, 'PrepareForSleep')
            forged.append(True, signature='b')
            attacker.send_message(forged)
            bad_type = dbus.lowlevel.SignalMessage(MANAGER_PATH, MANAGER, 'PrepareForSleep')
            bad_type.append('https://private.invalid/title', signature='s')
            producer.send_message(bad_type)
            variant = dbus.lowlevel.SignalMessage(MANAGER_PATH, MANAGER, 'PrepareForSleep')
            variant.append(dbus.Boolean(True, variant_level=1), signature='v')
            producer.send_message(variant)
            time.sleep(.05)
            assert controller.snapshot()['state'] == 'ACTIVE'
            assert focus.WindowChanged('code')
            wait_for(lambda: tracker.snapshot()['active_app'] == 'code')
            time.sleep(.03)
            assert session_adapter.stop()
            assert focus_adapter.stop(close_tracker=False)
            assert all(not bus.get_is_connected() for bus in focus_connections)
            events = list(vault.read_events())
            assert all(event.type == 'window_focus_sampled' for event in events)
            assert all(0 <= event.data['duracion'] < 1.0 for event in events)
            assert all(set(event.data) == {'aplicacion','categoria','duracion'} for event in events)
            assert 'private.invalid' not in json.dumps([e.to_dict() for e in events])
            assert vault.vault_path.stat().st_mode & 0o777 == 0o600
            summary.update({'native_signal_decoding':'PASS', 'lock_duplicate_and_recovery':'PASS',
                            'suspend_resume_and_new_focus':'PASS', 'sender_type_variant_rejection':'PASS',
                            'private_vault_permissions':'0600', 'focus_events':len(events),
                            'all_focus_durations_seconds':[e.data['duracion'] for e in events]})
    finally:
        cleanup_failed = False
        for adapter in (session_adapter, focus_adapter):
            if adapter is not None:
                try:
                    clean = adapter.stop(close_tracker=False) if adapter is focus_adapter else adapter.stop()
                    cleanup_failed = cleanup_failed or not clean
                except Exception:
                    cleanup_failed = True
        producer_name = None  # Release test name before closing its connection.
        for connection in connections:
            try:
                connection.close()
            except Exception:
                cleanup_failed = True
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        summary['owned_bus_process_terminated'] = process.poll() is not None
        if cleanup_failed:
            raise RuntimeError('isolated adapter cleanup incomplete')
    return summary


def main():
    results = {'host_read_only':host_probe(), 'isolated_synthetic_bus':isolated_probe()}
    print(json.dumps(results, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
