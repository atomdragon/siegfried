"""Opt-in logind/KDE signals. All native dependencies stay inside start().

No polling, names exported, desktop commands, inhibitors, subprocesses or
persistent files. A bounded worker keeps tracker/Vault I/O out of bus callbacks.
"""
from collections import deque
import logging
import os
from pathlib import Path
import re
import threading
import time

from siegfried.core.rest import ClockSample
from siegfried.daemon.session import SessionController

logger = logging.getLogger(__name__)
LOGIN = 'org.freedesktop.login1'
MANAGER_PATH = '/org/freedesktop/login1'
MANAGER = LOGIN + '.Manager'
SESSION = LOGIN + '.Session'
SAVER = 'org.freedesktop.ScreenSaver'
SAVER_PATH = '/ScreenSaver'
PROPS = 'org.freedesktop.DBus.Properties'
BROKER = 'org.freedesktop.DBus'
BROKER_PATH = '/org/freedesktop/DBus'


class SessionClock:
    def __init__(self):
        try:
            value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            self.boot_id = value if re.fullmatch(r'[a-f0-9-]{36}', value) else None
        except OSError:
            self.boot_id = None

    def __call__(self):
        try:
            boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        except (AttributeError, OSError):
            boot = None
        return ClockSample(time.time(), time.monotonic(), boot, self.boot_id)


class SessionDBusAdapter:
    MAX_PENDING = 32
    RATE_PER_SECOND = 20.0
    MAX_BURST = 40.0

    def __init__(self, controller: SessionController, clock=None):
        self.controller = controller
        self.clock = clock or SessionClock()
        self._condition = threading.Condition()
        self._lifecycle_lock = threading.Lock()
        self._pending = deque()
        self._running = False
        self._inflight = False
        self._worker = self._thread = self._loop = None
        self._buses = {}
        self._owners = {}
        self._matches = []
        self._native_bool = bool
        self._session_path = self._session_id = None
        self._initial_session_lost = False
        self._tokens = self.MAX_BURST
        self._token_time = time.monotonic()
        self._stop_sample = None
        self._shutdown_failed = False
        self._dispatch_ready = threading.Event()
        self.dropped = 0
        self.sources = {'logind': False, 'kde': False}

    def _boolean(self, value):
        if type(value) is bool or type(value) is self._native_bool:
            return bool(value)
        raise ValueError('invalid boolean')

    def _enqueue(self, kind, value, sample=None):
        sample = sample or self.clock()
        with self._condition:
            if not self._running:
                return False
            deny = (kind in ('degraded', 'session_lost') or
                    (kind in ('suspended', 'shutting_down', 'kde_locked', 'locked_hint') and value is True) or
                    (kind == 'active' and value is False))
            if deny:
                self.controller.tracker.pause_session_input()
            ticket = self.controller.tracker.session_gate[2]
            now = time.monotonic()
            self._tokens = min(self.MAX_BURST, self._tokens + (now-self._token_time)*self.RATE_PER_SECOND)
            self._token_time = now
            # Consecutive duplicates can be collapsed without losing any state boundary.
            if self._pending and self._pending[-1][:2] == (kind, value):
                old = self._pending[-1]
                self._pending[-1] = (kind, value, old[2], ticket)
                return True
            if len(self._pending) >= self.MAX_PENDING or self._tokens < 1:
                self.dropped += 1
                self._pending.clear()
                self.controller.tracker.pause_session_input()
                self._pending.append(('degraded', None, sample, self.controller.tracker.session_gate[2]))
                self._condition.notify_all()
                return False
            self._tokens -= 1
            self._pending.append((kind, value, sample, ticket))
            self._condition.notify_all()
        return True

    def _run_worker(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending or not self._running)
                if not self._pending:
                    break
                kind, value, sample, ticket = self._pending.popleft()
                self._inflight = True
            try:
                if kind == 'snapshot':
                    self.controller.initialize(value, sample, ticket)
                else:
                    self.controller.handle(kind, value, sample, ticket)
            except Exception:
                logger.warning('Session transition failed; observability degraded')
                self.controller.tracker.pause_session_input()
                try:
                    self.controller.handle('degraded', None, sample)
                except Exception:
                    pass  # Admission remains gated even if storage is unavailable.
            finally:
                with self._condition:
                    self._inflight = False
                    self._condition.notify_all()
        if self._stop_sample is not None:
            self._close_controller()

    def _close_controller(self):
        try:
            self.controller.stop(self._stop_sample)
        except Exception:
            self._shutdown_failed = True
            logger.warning('Session shutdown failed; cleanup incomplete')

    def wait_idle(self, timeout=2.0):
        """Bounded synchronization for verification, not a polling detector."""
        with self._condition:
            return self._condition.wait_for(lambda: not self._pending and not self._inflight, timeout)

    def _signal(self, source, kind, value, sender):
        if not self._running or not sender or str(sender) != self._owners.get(source):
            return False
        if getattr(value, 'variant_level', 0) != 0:
            return False
        try:
            parsed = self._boolean(value)
        except ValueError:
            return False
        return self._enqueue(kind, parsed)

    def _properties(self, interface, changed, invalidated, sender=None):
        if not self._running or str(sender) != self._owners.get('logind') or str(interface) != SESSION:
            return False
        if not isinstance(changed, dict) or not isinstance(invalidated, (list, tuple)):
            return False
        if len(changed) > 32 or len(invalidated) > 32:
            return False
        relevant = {'Active': 'active', 'LockedHint': 'locked_hint'}
        parsed = []
        try:
            for key, kind in relevant.items():
                if key in changed:
                    parsed.append((kind, self._boolean(changed[key])))
        except ValueError:
            return False
        if 'State' in changed:
            if changed['State'] not in ('active', 'online', 'closing'):
                return False
            if changed['State'] == 'closing':
                parsed.append(('session_lost', None))
        if any(key in relevant or key == 'State' for key in invalidated):
            parsed.append(('degraded', None))
        sample = self.clock()
        return all(self._enqueue(kind, value, sample) for kind, value in parsed)

    def _removed(self, session_id, path, sender=None):
        if (self._running and str(sender) == self._owners.get('logind') and
                str(session_id) == self._session_id and str(path) == self._session_path):
            self._enqueue('session_lost', None)

    def _disconnected(self, connection):
        for source, bus in self._buses.items():
            if connection is bus:
                self._owners[source] = None
                self.sources[source] = False
                self._enqueue('degraded', None)
                return

    @staticmethod
    def _call(bus, name, path, interface, member, signature='', args=()):
        return bus.call_blocking(name, path, interface, member, signature, args, timeout=1.0)

    def _owner(self, bus, name, uid):
        owner = str(self._call(bus, BROKER, BROKER_PATH, BROKER, 'GetNameOwner', 's', (name,)))
        actual = self._call(bus, BROKER, BROKER_PATH, BROKER, 'GetConnectionUnixUser', 's', (owner,))
        if int(actual) != uid:
            raise ValueError('untrusted owner uid')
        return owner

    def _watch_owner(self, bus, source, name):
        def changed(bus_name, old, new):
            if str(new) != self._owners.get(source):
                self._owners[source] = None
                self.sources[source] = False
                self._enqueue('degraded', None)
        self._matches.append(bus.add_signal_receiver(changed, signal_name='NameOwnerChanged',
            dbus_interface=BROKER, bus_name=BROKER, path=BROKER_PATH, arg0=name))

    def _subscribe(self, bus, source, interface, path, member, callback):
        self._matches.append(bus.add_signal_receiver(callback, signal_name=member,
            dbus_interface=interface, bus_name=self._owners[source], path=path,
            sender_keyword='sender'))

    def _install_logind(self, bus, dbus, values):
        owner = self._owner(bus, LOGIN, 0)
        self._owners['logind'] = owner
        self._watch_owner(bus, 'logind', LOGIN)
        for member, kind in (('PrepareForSleep', 'suspended'), ('PrepareForShutdown', 'shutting_down')):
            self._subscribe(bus, 'logind', MANAGER, MANAGER_PATH, member,
                lambda value, sender=None, kind=kind: self._signal('logind', kind, value, sender))
        get = lambda path, iface, key: self._call(bus, owner, path, PROPS, 'Get', 'ss', (iface, key))
        values['suspended'] = self._boolean(get(MANAGER_PATH, MANAGER, 'PreparingForSleep'))
        values['shutting_down'] = self._boolean(get(MANAGER_PATH, MANAGER, 'PreparingForShutdown'))
        try:
            path = self._call(bus, owner, MANAGER_PATH, MANAGER, 'GetSessionByPID', 'u', (dbus.UInt32(os.getpid()),))
        except dbus.DBusException:
            sid = os.environ.get('XDG_SESSION_ID', '')
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', sid):
                raise ValueError('no validated graphical session')
            path = self._call(bus, owner, MANAGER_PATH, MANAGER, 'GetSession', 's', (sid,))
        user = get(path, SESSION, 'User')
        if (int(user[0]) != os.getuid() or str(get(path, SESSION, 'Type')) not in ('wayland', 'x11') or
                str(get(path, SESSION, 'Class')) not in ('user', 'user-early')):
            raise ValueError('untrusted graphical session')
        self._session_path = str(path)
        self._session_id = str(get(path, SESSION, 'Id'))
        self._subscribe(bus, 'logind', PROPS, self._session_path, 'PropertiesChanged', self._properties)
        self._subscribe(bus, 'logind', MANAGER, MANAGER_PATH, 'SessionRemoved', self._removed)
        values['active'] = self._boolean(get(path, SESSION, 'Active'))
        values['locked_hint'] = self._boolean(get(path, SESSION, 'LockedHint'))
        if str(get(path, SESSION, 'State')) == 'closing':
            values['active'] = False
            self._initial_session_lost = True
        if self._owner(bus, LOGIN, 0) != owner:
            raise ValueError('logind owner changed during startup')
        self.sources['logind'] = True

    def _install_kde(self, bus, values):
        owner = self._owner(bus, SAVER, os.getuid())
        self._owners['kde'] = owner
        self._watch_owner(bus, 'kde', SAVER)
        self._subscribe(bus, 'kde', SAVER, SAVER_PATH, 'ActiveChanged',
                        lambda value, sender=None: self._signal('kde', 'kde_locked', value, sender))
        values['kde_locked'] = self._boolean(self._call(bus, owner, SAVER_PATH, SAVER, 'GetActive'))
        if self._owner(bus, SAVER, os.getuid()) != owner:
            raise ValueError('KDE owner changed during startup')
        self.sources['kde'] = True

    def start(self):
        with self._lifecycle_lock:
            if self._running:
                return True
            if os.environ.get('SIEGFRIED_ENABLE_SESSION') != '1':
                return False
            if any(t and t.is_alive() for t in (self._thread, self._worker)):
                return False
            self.controller.begin(self.clock())
            try:
                import dbus
                from dbus.mainloop.glib import DBusGMainLoop, threads_init
                from gi.repository import GLib
                threads_init()
                self._native_bool = dbus.Boolean
            except Exception:
                logger.info('Optional native session bindings unavailable; core remains operational')
                return False
            with self._condition:
                self._pending.clear()
                self._inflight = False
            self._stop_sample = None
            self._shutdown_failed = False
            self._initial_session_lost = False
            self._tokens, self._token_time = self.MAX_BURST, time.monotonic()
            values = dict.fromkeys(self.controller.KEYS)
            try:
                for source, env_key, factory in (('logind', 'DBUS_SYSTEM_BUS_ADDRESS', dbus.SystemBus),
                                                  ('kde', 'DBUS_SESSION_BUS_ADDRESS', dbus.SessionBus)):
                    address = os.environ.get(env_key, '')
                    if (address and (not address.startswith('unix:') or ';' in address)) or (source == 'kde' and not address):
                        continue
                    try:
                        bus = (dbus.bus.BusConnection(address, mainloop=DBusGMainLoop()) if address
                               else factory(private=True, mainloop=DBusGMainLoop()))
                        bus.set_exit_on_disconnect(False)
                        self._buses[source] = bus
                        bus.call_on_disconnection(self._disconnected)
                        if source == 'logind':
                            self._install_logind(bus, dbus, values)
                        else:
                            self._install_kde(bus, values)
                    except Exception:
                        logger.info('Optional %s session source unavailable', source)
                if not any(self.sources.values()):
                    self._release_connections()
                    return False
                self._loop = GLib.MainLoop()
                self._dispatch_ready.clear()
                def ready():
                    self._dispatch_ready.set()
                    return False
                GLib.idle_add(ready)
                self._running = True
                self._enqueue('snapshot', values)
                if self._initial_session_lost:
                    self._enqueue('session_lost', None)
                self._worker = threading.Thread(target=self._run_worker, name='SiegfriedSessionWorker', daemon=True)
                self._thread = threading.Thread(target=self._run_loop, name='SiegfriedSessionDBus', daemon=True)
                self._worker.start()
                self._thread.start()
                if not self._dispatch_ready.wait(1.0):
                    raise RuntimeError('session dispatcher startup timeout')
                return True
            except Exception:
                logger.warning('Optional session adapter failed to start')
                self._running = False
                self._release_connections()
                if self._loop is not None:
                    self._loop.quit()
                with self._condition:
                    self._condition.notify_all()
                return False

    def _run_loop(self):
        try:
            self._loop.run()
        finally:
            if self._running:
                self._enqueue('degraded', None)

    def _release_connections(self):
        clean = True
        for match in self._matches:
            try:
                match.remove()
            except Exception:
                clean = False
        self._matches.clear()
        buses, self._buses = self._buses, {}
        self._owners.clear()
        self.sources = {'logind': False, 'kde': False}
        for bus in buses.values():
            try:
                bus.close()
            except Exception:
                clean = False
        return clean

    def stop(self, timeout=1.0):
        with self._lifecycle_lock:
            with self._condition:
                self._running = False
                self._stop_sample = self.clock()
                self._condition.notify_all()
            self.controller.tracker.pause_session_input() if self.controller.enabled else None
            clean = self._release_connections()
            if self._loop is not None:
                self._loop.quit()
            deadline = time.monotonic() + max(0, timeout)
            for thread in (self._thread, self._worker):
                if thread and thread is not threading.current_thread() and thread.is_alive():
                    thread.join(max(0, deadline-time.monotonic()))
                    clean = clean and not thread.is_alive()
            if self._worker is None or not self._worker.is_alive():
                self._close_controller()
            return clean and not self._shutdown_failed

    def is_running(self):
        return self._running
