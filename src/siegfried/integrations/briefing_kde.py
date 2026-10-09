"""Optional native actionable notification. No domain or health state mutation."""
import html
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

from siegfried.integrations.notifications import DesktopNotificationSender

NAME = 'org.freedesktop.Notifications'
PATH = '/org/freedesktop/Notifications'
BROKER = 'org.freedesktop.DBus'
SAVER = 'org.freedesktop.ScreenSaver'


def trusted_executable(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        return False
    for item in (path, *path.parents):
        st = item.stat()
        if st.st_uid not in (0, os.getuid()) or st.st_mode & 0o022:
            # Root's sticky temporary directory is safe when descendants are owned.
            if not (item.is_dir() and st.st_uid == 0 and st.st_mode & stat.S_ISVTX):
                return False
    return True


class REPLLauncher:
    """Fixed argv from trusted executables; notification text never enters argv."""
    def __init__(self, repository):
        self.cli = Path(repository) / 'bin' / 'siegfried'
        self.konsole = shutil.which('konsole')
        self.argv = [self.konsole, '--separate', '-e', sys.executable, str(self.cli)] if self.konsole else []

    def available(self):
        return bool(self.argv and all(trusted_executable(p) for p in (self.konsole, sys.executable, self.cli)))

    def __call__(self, token=''):
        if not self.available():
            return False
        env = dict(os.environ)
        env.pop('XDG_ACTIVATION_TOKEN', None)
        env.pop('DESKTOP_STARTUP_ID', None)
        if isinstance(token, str) and 0 < len(token) <= 1024 and token.isprintable():
            env['XDG_ACTIVATION_TOKEN'] = token
        try:
            # This terminal belongs to the user's explicit session, not to a worker.
            subprocess.Popen(self.argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=env, close_fds=True, start_new_session=True)
            return True
        except OSError:
            return False


class KDEBriefingPresenter:
    def __init__(self, on_open=None):
        self.on_open = on_open
        self.bus = self.loop = self.GLib = self.dbus = None
        self.owner = self.saver_owner = None
        self.matches = []
        self.sources = []
        self.notification_id = None
        self.unlocked = False
        self.actions = False
        self.inhibited = None
        self.opened = False
        self.disconnected = False
        self.token = ''
        self.fallback = ''
        self.expiry_ms = 12000
        self.sent_at = None

    def _call(self, name, path, interface, method, signature='', args=()):
        return self.bus.call_blocking(name, path, interface, method, signature, args, timeout=.2)

    def _owner(self, name):
        owner = str(self._call(BROKER, '/org/freedesktop/DBus', BROKER, 'GetNameOwner', 's', (name,)))
        uid = self._call(BROKER, '/org/freedesktop/DBus', BROKER, 'GetConnectionUnixUser', 's', (owner,))
        if int(uid) != os.getuid():
            raise ValueError('untrusted_desktop_owner')
        return owner

    def start(self):
        address = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
        if not address.startswith('unix:') or ';' in address:
            return False
        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop, threads_init
            from gi.repository import GLib
            threads_init()
            self.dbus, self.GLib = dbus, GLib
            self.bus = dbus.bus.BusConnection(address, mainloop=DBusGMainLoop())
            self.bus.set_exit_on_disconnect(False)
            self.bus.call_on_disconnection(self._disconnect)
            self.owner = self._owner(NAME)
            caps = self._call(self.owner, PATH, NAME, 'GetCapabilities')
            if len(caps) > 32 or any(not isinstance(c, str) or len(c) > 64 for c in caps):
                raise ValueError('invalid_desktop_capabilities')
            self.actions = 'actions' in caps and self.on_open is not None
            try:
                self.inhibited = bool(self._call(self.owner, PATH, 'org.freedesktop.DBus.Properties',
                                                'Get', 'ss', (NAME, 'Inhibited')))
            except Exception:
                pass
            self.loop = GLib.MainLoop()
            for signal, callback in (('ActionInvoked', self._action), ('NotificationClosed', self._closed),
                                     ('ActivationToken', self._activation)):
                self.matches.append(self.bus.add_signal_receiver(callback, signal_name=signal,
                    dbus_interface=NAME, bus_name=self.owner, path=PATH, sender_keyword='sender'))
            self.matches.append(self.bus.add_signal_receiver(self._owner_changed, signal_name='NameOwnerChanged',
                dbus_interface=BROKER, bus_name=BROKER, path='/org/freedesktop/DBus'))
            try:
                self.saver_owner = self._owner(SAVER)
                self.matches.append(self.bus.add_signal_receiver(self._lock_changed, signal_name='ActiveChanged',
                    dbus_interface=SAVER, bus_name=self.saver_owner, path='/ScreenSaver', sender_keyword='sender'))
                self.refresh_unlocked()
            except Exception:
                self.unlocked = False
            if self._owner(NAME) != self.owner:
                raise ValueError('notification_owner_changed')
            return True
        except Exception:
            self.close()
            return False

    def refresh_unlocked(self):
        self.unlocked = False
        try:
            if self._owner(SAVER) != self.saver_owner:
                return False
            locked = self._call(self.saver_owner, '/ScreenSaver', SAVER, 'GetActive')
            if type(locked) not in (bool, self.dbus.Boolean):
                return False
            self.unlocked = not bool(locked)
        except Exception:
            pass
        return self.unlocked

    def send(self, text, fallback, *, expiry_ms=12000):
        if not self.bus or self.disconnected:
            return False
        self.fallback, self.expiry_ms = fallback, expiry_ms
        self.refresh_unlocked()
        body = text if self.unlocked else fallback
        try:
            result = self._call(self.owner, PATH, NAME, 'Notify', 'susssasa{sv}i',
                ('Siegfried', self.notification_id or 0, 'dialog-information', 'Siegfried — Inicio',
                 html.escape(body), ['open', 'Abrir sesión'] if self.actions else [],
                 {'urgency':self.dbus.Byte(1), 'transient':self.dbus.Boolean(True),
                  'suppress-sound':self.dbus.Boolean(True)}, expiry_ms))
            if type(result) not in (int, self.dbus.UInt32) or not 0 < int(result) < 2**32:
                return False
            self.notification_id = int(result)
            if self.sent_at is None:
                self.sent_at = time.monotonic()
            return True
        except Exception:
            return False

    def _valid_signal(self, notification_id, sender):
        return (str(sender) == self.owner and type(notification_id) in (int, self.dbus.UInt32)
                and getattr(notification_id, 'variant_level', 0) == 0
                and notification_id == self.notification_id and not self.disconnected)

    def _activation(self, notification_id, token, sender=None):
        if (self._valid_signal(notification_id, sender) and isinstance(token, str)
                and 0 < len(token) <= 1024 and token.isprintable()):
            self.token = str(token)

    def _action(self, notification_id, action, sender=None):
        if (not self._valid_signal(notification_id, sender) or action != 'open' or
                getattr(action, 'variant_level', 0) != 0 or not self.actions or self.opened):
            return
        if not self.refresh_unlocked():
            return
        self.opened = True
        try:
            self.on_open(self.token)
        except Exception:
            pass  # No command, token or exception payload goes to desktop logs.
        finally:
            self.loop.quit()

    def _closed(self, notification_id, reason, sender=None):
        if self._valid_signal(notification_id, sender):
            self.notification_id = None
            self.loop.quit()

    def _lock_changed(self, locked, sender=None):
        if (str(sender) == self.saver_owner and type(locked) in (bool, self.dbus.Boolean)
                and getattr(locked, 'variant_level', 0) == 0):
            self.unlocked = not bool(locked)
            if locked and self.notification_id:
                self.send(self.fallback, self.fallback, expiry_ms=self.expiry_ms)

    def _owner_changed(self, name, old, new):
        if str(name) == NAME and str(new) != self.owner:
            self._disconnect(self.bus)
        elif str(name) == SAVER and str(new) != self.saver_owner:
            self.unlocked = False
            self.saver_owner = None
            if self.notification_id:
                self.send(self.fallback, self.fallback, expiry_ms=self.expiry_ms)

    def _disconnect(self, connection):
        if connection is self.bus:
            self.disconnected = True
            self.unlocked = False
            if self.loop:
                self.loop.quit()

    def wait(self, seconds=12):
        if not self.loop or self.disconnected or self.notification_id is None:
            return
        seconds = min(30., max(.01, seconds))
        self.sources.append(self.GLib.timeout_add(max(1, int(seconds * 1000)), self._expire))
        # Also handles a quit/close delivered before run, without a polling loop.
        self.sources.append(self.GLib.idle_add(self._ready))
        self.loop.run()

    def dispatch_pending(self):
        """One bounded drain after optional I/O, not a periodic polling detector."""
        if self.GLib:
            context = self.GLib.MainContext.default()
            for _ in range(16):
                if not context.pending():
                    break
                context.iteration(False)

    def _ready(self):
        if self.opened or self.disconnected or self.notification_id is None:
            self.loop.quit()
        return False

    def _expire(self):
        self.loop.quit()
        return False

    def close(self):
        if self.notification_id and self.bus and not self.disconnected:
            try:
                self._call(self.owner, PATH, NAME, 'CloseNotification', 'u', (self.notification_id,))
            except Exception:
                pass
        self.notification_id = None
        for match in self.matches:
            try:
                match.remove()
            except Exception:
                pass
        self.matches.clear()
        if self.GLib:
            for source in self.sources:
                context = self.GLib.MainContext.default()
                if context.find_source_by_id(source):
                    self.GLib.source_remove(source)
        self.sources.clear()
        if self.loop:
            self.loop.quit()
        if self.bus:
            try:
                self.bus.close()
            except Exception:
                pass
            self.bus = None


def fallback_notification(text):
    """Existing no-action sender; plain local output when no desktop is reachable."""
    address = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
    if address.startswith('unix:') and ';' not in address:
        sender = DesktopNotificationSender()
        if sender.send('Siegfried — Inicio', html.escape(text)):
            return True
    print(text + ' Abra siegfried en una consola cuando lo desee.')
    return False
