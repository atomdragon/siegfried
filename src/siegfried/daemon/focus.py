"""Deterministic Window Focus Tracking and KWin Event-Driven Integration.

Conforms to:
- Event Schema v1 (EventType.WINDOW_FOCUS_SAMPLED)
- Python Standard Library exclusively for runtime core
- Strict local privacy: NO window titles, captions, URLs, or document paths
- Monotonic interval timing with suspension & null-focus resilience
"""

from collections import deque
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple

from siegfried.contracts.events import Event, EventType
from siegfried.storage.vault import Vault


logger = logging.getLogger("siegfried.daemon.focus")

# Regex allowing strictly lowercase alphanumeric, period, underscore, and hyphen
_SAFE_APP_REGEX = re.compile(r"^[a-z0-9_\.\-]+$")

# Disallowed substrings that indicate titles, paths, commands, or URLs leaked in app id
_SUSPICIOUS_SUBSTRINGS = (
    "/", "\\", "http:", "https:", "file:", "<", ">", ";", "&", "|", "`", "$",
    "untitled", "document", "private browsing", "incognito", "\n", "\r", "\t", "\x00",
)

# Known application identifier to deterministic category mapping
DEFAULT_APP_CATEGORIES: Dict[str, str] = {
    # Desarrollo
    "code": "desarrollo",
    "code_code": "desarrollo",
    "code-oss": "desarrollo",
    "vscode": "desarrollo",
    "vscodium": "desarrollo",
    "cursor": "desarrollo",
    "pycharm": "desarrollo",
    "sublime_text": "desarrollo",
    "kate": "desarrollo",
    "kwrite": "desarrollo",
    "neovim": "desarrollo",
    "nvim": "desarrollo",
    "vim": "desarrollo",
    "emacs": "desarrollo",
    "gedit": "desarrollo",
    "clion": "desarrollo",
    "intellij": "desarrollo",
    "eclipse": "desarrollo",
    "gitkraken": "desarrollo",
    # Terminal
    "konsole": "terminal",
    "org.kde.konsole": "terminal",
    "kitty": "terminal",
    "alacritty": "terminal",
    "gnome-terminal": "terminal",
    "terminator": "terminal",
    "xterm": "terminal",
    "foot": "terminal",
    "wezterm": "terminal",
    "tilix": "terminal",
    # Navegación
    "firefox": "navegacion",
    "firefox-esr": "navegacion",
    "org.mozilla.firefox": "navegacion",
    "chromium": "navegacion",
    "chromium-browser": "navegacion",
    "google-chrome": "navegacion",
    "google-chrome-stable": "navegacion",
    "brave-browser": "navegacion",
    "microsoft-edge": "navegacion",
    "opera": "navegacion",
    "vivaldi": "navegacion",
    # Comunicación
    "slack": "comunicacion",
    "discord": "comunicacion",
    "telegram-desktop": "comunicacion",
    "org.telegram.desktop": "comunicacion",
    "teams": "comunicacion",
    "thunderbird": "comunicacion",
    "zoom": "comunicacion",
    "element": "comunicacion",
    # Ofimática / Lectura
    "libreoffice": "ofimatica",
    "soffice": "ofimatica",
    "obsidian": "ofimatica",
    "notion": "ofimatica",
    "okular": "ofimatica",
    "org.kde.okular": "ofimatica",
    "evince": "ofimatica",
    "zathura": "ofimatica",
    # Multimedia / Diseño
    "vlc": "multimedia",
    "mpv": "multimedia",
    "spotify": "multimedia",
    "gimp": "multimedia",
    "inkscape": "multimedia",
    "kdenlive": "multimedia",
    "audacity": "multimedia",
    "obs": "multimedia",
    # Sistema
    "systemsettings": "sistema",
    "org.kde.systemsettings": "sistema",
    "dolphin": "sistema",
    "org.kde.dolphin": "sistema",
    "plasma-systemmonitor": "sistema",
    "htop": "sistema",
    "btop": "sistema",
    "krunner": "sistema",
    "ksysguard": "sistema",
}


def normalize_and_categorize(
    raw_app: Optional[str],
    category_map: Optional[Dict[str, str]] = None,
) -> Tuple[str, str]:
    """Deterministically sanitize, normalize, and categorize an application identifier.

    Strict privacy guarantees:
    - Never retains window titles, URLs, or command arguments.
    - If input contains illegal characters or suspicious markers, falls back to 'desconocida'.
    - Returns (normalized_app, category).
    """
    if not isinstance(raw_app, str) or not raw_app.strip():
        return ("desconocida", "desconocida")

    text = raw_app.strip().lower()

    # Reject if length exceeds safety threshold
    if len(text) > 64:
        return ("desconocida", "desconocida")

    # Reject if any suspicious string pattern is detected
    for sub in _SUSPICIOUS_SUBSTRINGS:
        if sub in text:
            return ("desconocida", "desconocida")

    # Strip .desktop suffix if present
    if text.endswith(".desktop"):
        text = text[:-8].strip()

    # Normalize KDE reverse-domain prefixes if known
    if text.startswith("org.kde."):
        short = text[8:]
        if short:
            text = short

    # Strip _code or repeated vendor stems
    if text == "code_code":
        text = "code"

    # Enforce strict regex whitelist
    if not _SAFE_APP_REGEX.match(text):
        return ("desconocida", "desconocida")

    cat_map = category_map if category_map is not None else DEFAULT_APP_CATEGORIES
    category = cat_map.get(text, "desconocida")
    return (text, category)


class FocusTracker:
    """Deterministic window focus tracking, duration accounting, and Vault event generation."""

    def __init__(
        self,
        vault: Optional[Vault] = None,
        category_map: Optional[Dict[str, str]] = None,
        min_duration_sec: float = 0.0,
    ) -> None:
        self.vault = vault
        self.category_map = category_map or DEFAULT_APP_CATEGORIES
        self.min_duration_sec = max(0.0, float(min_duration_sec))
        self._lock = threading.RLock()

        # State of current active focus window
        self._current_app: Optional[str] = None
        self._current_category: Optional[str] = None
        self._start_monotonic: Optional[float] = None
        self._start_wall: Optional[float] = None
        self._is_active: bool = True
        self._session_allowed = True
        self._session_boundary: Optional[float] = None
        self._focus_epoch = 0
        self._gate_lock = threading.Lock()
        self._gate_ticket = 0
        self._session_input = threading.Event()
        self._session_input.set()

    def pause_session_input(self) -> int:
        """Fast admission barrier, safe in a bus callback; no tracker/disk lock."""
        with self._gate_lock:
            self._gate_ticket += 1
            self._session_input.clear()
            return self._gate_ticket

    @property
    def session_gate(self) -> Tuple[bool, int, int]:
        with self._gate_lock:
            return self._session_input.is_set(), self._focus_epoch, self._gate_ticket

    def set_session_allowed(self, allowed: bool, monotonic_now: Optional[float] = None,
                            wall_now: Optional[float] = None, *, discard: bool = False,
                            gate_ticket: Optional[int] = None) -> Optional[Event]:
        """Session policy gates focus without changing the user's enable preference."""
        with self._lock:
            event = None
            if self._session_allowed != allowed or discard:
                self._session_boundary = monotonic_now if monotonic_now is not None else time.monotonic()
                if discard:
                    self._current_app = self._current_category = None
                    self._start_monotonic = self._start_wall = None
                elif not allowed:
                    event = self._on_window_changed_locked(None, monotonic_now, wall_now)
                self._session_allowed = allowed
                with self._gate_lock:
                    self._focus_epoch += 1
            with self._gate_lock:
                if allowed and (gate_ticket is None or gate_ticket == self._gate_ticket):
                    self._session_input.set()
                elif not allowed:
                    self._session_input.clear()
            return event

    def _on_window_changed_locked(
        self,
        raw_app: Optional[str],
        monotonic_now: Optional[float] = None,
        wall_now: Optional[float] = None,
    ) -> Optional[Event]:
        """Internal window focus transition logic executed with self._lock held."""
        if not self._is_active and raw_app is not None:
            return None

        now_mono = monotonic_now if monotonic_now is not None else time.monotonic()
        now_wall = wall_now if wall_now is not None else time.time()

        # 1. Deduplication check: if exactly identical application is focused, preserve interval
        is_null_focus = not raw_app or str(raw_app).strip() in ("", "__null__", "null", "none")
        if not is_null_focus:
            app_name, category = normalize_and_categorize(raw_app, self.category_map)
            if (
                app_name == self._current_app
                and category == self._current_category
                and self._start_monotonic is not None
            ):
                return None
        else:
            app_name, category = "desconocida", "desconocida"

        recorded_event: Optional[Event] = None

        # 2. Close current active interval if exists
        if self._current_app is not None and self._start_monotonic is not None:
            duration = now_mono - self._start_monotonic
            duration = max(0.0, duration)

            if duration >= self.min_duration_sec:
                event_data = {
                    "aplicacion": self._current_app,
                    "categoria": self._current_category or "desconocida",
                    "duracion": round(duration, 2),
                }
                ts = self._start_wall if self._start_wall is not None else now_wall
                recorded_event = Event.create(EventType.WINDOW_FOCUS_SAMPLED, event_data, ts=ts)
                if self.vault is not None:
                    self.vault.append(recorded_event)
                logger.debug(
                    f"Focus sampled: app={self._current_app} "
                    f"cat={self._current_category} dur={duration:.2f}s"
                )

        # 3. Determine new active window
        if is_null_focus:
            self._current_app = None
            self._current_category = None
            self._start_monotonic = None
            self._start_wall = None
        else:
            self._current_app = app_name
            self._current_category = category
            self._start_monotonic = now_mono
            self._start_wall = now_wall

        return recorded_event

    def on_window_changed(
        self,
        raw_app: Optional[str],
        monotonic_now: Optional[float] = None,
        wall_now: Optional[float] = None,
        *, epoch: Optional[int] = None,
    ) -> Optional[Event]:
        """Handle window focus transition (App A -> App B or null focus)."""
        with self._lock:
            if raw_app is not None:
                if (not self._session_input.is_set() or not self._session_allowed or
                        (epoch is not None and epoch != self._focus_epoch) or
                        (monotonic_now is not None and self._session_boundary is not None
                         and monotonic_now < self._session_boundary)):
                    return None
            return self._on_window_changed_locked(raw_app, monotonic_now=monotonic_now, wall_now=wall_now)

    def on_suspend(
        self,
        monotonic_now: Optional[float] = None,
        wall_now: Optional[float] = None,
    ) -> Optional[Event]:
        """Handle screen lock or system suspend. Closes active interval to prevent counting sleep time."""
        with self._lock:
            if self._current_app is None or self._start_monotonic is None:
                return None
            return self._on_window_changed_locked(None, monotonic_now=monotonic_now, wall_now=wall_now)

    def on_resume(
        self,
        monotonic_now: Optional[float] = None,
        wall_now: Optional[float] = None,
    ) -> None:
        """Handle system resume. Resets state so next window switch starts fresh."""
        with self._lock:
            self._current_app = None
            self._current_category = None
            self._start_monotonic = None
            self._start_wall = None

    def close_active_interval(
        self,
        monotonic_now: Optional[float] = None,
        wall_now: Optional[float] = None,
    ) -> Optional[Event]:
        """Close active interval and persist to Vault (e.g., upon daemon shutdown)."""
        with self._lock:
            return self._on_window_changed_locked(None, monotonic_now=monotonic_now, wall_now=wall_now)

    def set_active(self, active: bool) -> None:
        """Enable or disable focus tracking."""
        with self._lock:
            if not active and self._current_app is not None:
                self._on_window_changed_locked(None)
            self._is_active = bool(active)

    def snapshot(self) -> Dict[str, Any]:
        """Return deterministic snapshot of current focus state."""
        with self._lock:
            elapsed = 0.0
            if self._start_monotonic is not None:
                elapsed = max(0.0, time.monotonic() - self._start_monotonic)
            return {
                "active_app": self._current_app,
                "active_category": self._current_category,
                "elapsed_seconds": round(elapsed, 2),
                "is_active": self._is_active,
                "session_allowed": self._session_allowed and self._session_input.is_set(),
            }


class FocusDBusAdapter:
    """Optional native binding adapter; core imports remain standard-library only.

    Enable explicitly with SIEGFRIED_ENABLE_KWIN=1. Distribution bindings are
    an authorized optional integration exception, never a core dependency.
    """

    DBUS_SERVICE_NAME = "org.siegfried.FocusWatcher"
    DBUS_OBJECT_PATH = "/FocusWatcher"
    DBUS_INTERFACE = "org.siegfried.FocusWatcher"
    MAX_PENDING = 64
    RATE_PER_SECOND = 20.0
    MAX_BURST = 40.0

    def __init__(self, tracker: FocusTracker) -> None:
        self.tracker = tracker
        self._running = False
        self._loop: Any = None
        self._thread: Optional[threading.Thread] = None
        self._worker: Optional[threading.Thread] = None
        self._service_object: Any = None
        self._bus: Any = None
        self._bus_name: Any = None
        self._owner_match: Any = None
        self._kwin_owner: Optional[str] = None
        self._condition = threading.Condition()
        self._pending: Any = deque()
        self._generation = 0
        self._last_app: Optional[str] = None
        self._tokens = self.MAX_BURST
        self._token_time = time.monotonic()
        self._tracker_epoch = self.tracker.session_gate[1]
        self._owns_loop = True

    def _accept_message(self, app_name: str, sender: Optional[str]) -> bool:
        """Bus supplies sender; reject before copying, logging, or persistence."""
        if not self._running or not sender or sender != self._kwin_owner:
            return False
        if not isinstance(app_name, str) or len(app_name) > 64:
            return False
        if app_name and (not _SAFE_APP_REGEX.fullmatch(app_name) or
                         normalize_and_categorize(app_name)[0] == "desconocida"):
            return False
        mono, wall = time.monotonic(), time.time()
        allowed, epoch, _ = self.tracker.session_gate
        if not allowed:
            return False
        with self._condition:
            if not self._running:
                return False
            if self._tracker_epoch != epoch:
                self._tracker_epoch = epoch
                self._last_app = None
            if app_name == self._last_app:
                return True
            self._tokens = min(self.MAX_BURST,
                               self._tokens + (mono - self._token_time) * self.RATE_PER_SECOND)
            self._token_time = mono
            if self._tokens < 1 or len(self._pending) >= self.MAX_PENDING:
                # Missing transitions cannot be accounted as continuous activity.
                self._generation += 1
                self._pending.clear()
                self._last_app = None
                self._condition.notify_all()
                return False
            self._tokens -= 1
            self._last_app = app_name
            self._pending.append((self._generation, app_name, mono, wall, epoch))
            self._condition.notify_all()
        return True

    def _run_worker(self) -> None:
        generation = self._generation
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending or not self._running or
                                         generation != self._generation)
                changed = generation != self._generation
                generation = self._generation
                item = self._pending.popleft() if self._pending else None
                done = not self._running and item is None
            if changed:
                self.tracker.on_resume()  # Discard uncertain interval after loss.
            if done:
                return
            if item is not None and item[0] == generation:
                try:
                    self.tracker.on_window_changed(item[1], item[2], item[3],
                                                   epoch=item[4] if len(item) > 4 else None)
                except Exception:
                    # Never include attacker-controlled data or exception text.
                    logger.warning("Focus persistence failed; interval discarded")
                    self.tracker.on_resume()

    def start(self, mainloop_owner: Any = None) -> bool:
        if self._running:
            return True
        if os.environ.get("SIEGFRIED_ENABLE_KWIN") != "1":
            return False
        address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        if not address.startswith("unix:") or ";" in address:
            return False
        if any(t and t.is_alive() for t in (self._thread, self._worker)):
            return False
        try:
            import dbus
            import dbus.service
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
        except Exception:
            logger.info("Optional native D-Bus bindings unavailable; focus disabled")
            return False
        try:
            self._bus = dbus.bus.BusConnection(address, mainloop=DBusGMainLoop())
            self._bus.set_exit_on_disconnect(False)
            owner = self._bus.get_name_owner("org.kde.KWin")
            pid = self._bus.call_blocking(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (owner,))
            if self._bus.get_unix_user(owner) != os.getuid() or int(pid) <= 0:
                raise RuntimeError("Untrusted session owner credentials")
            # Owner identity is authenticated by the bus, not executable provenance.
            # Same-UID KWin scripting/name squatting remain trust-boundary risks.
            self._kwin_owner = str(owner)
            adapter = self

            class _FocusService(dbus.service.Object):
                @dbus.service.method(adapter.DBUS_INTERFACE, in_signature="s",
                                     out_signature="b", sender_keyword="sender", message_keyword="message")
                def WindowChanged(self, app_name: str, sender: Optional[str] = None,
                                  message: Any = None) -> bool:
                    if message is None or str(message.get_signature()) != "s":
                        return False
                    return adapter._accept_message(app_name, sender)

            self._bus_name = dbus.service.BusName(self.DBUS_SERVICE_NAME, self._bus,
                                                 do_not_queue=True)
            self._service_object = _FocusService(self._bus, self.DBUS_OBJECT_PATH)
            self._owner_match = self._bus.add_signal_receiver(
                self._owner_changed, signal_name="NameOwnerChanged",
                dbus_interface="org.freedesktop.DBus", bus_name="org.freedesktop.DBus",
                path="/org/freedesktop/DBus", arg0="org.kde.KWin")
            # Close the get-owner/subscription race before accepting messages.
            if str(self._bus.get_name_owner("org.kde.KWin")) != self._kwin_owner:
                raise RuntimeError("KWin owner changed during startup")
            self._bus.call_on_disconnection(self._disconnected)
            self._owns_loop = mainloop_owner is None
            self._loop = GLib.MainLoop() if self._owns_loop else mainloop_owner._loop
            self._last_app = None
            self._tokens = self.MAX_BURST
            self._token_time = time.monotonic()
            self.tracker.on_resume()
            self._running = True
            self._worker = threading.Thread(target=self._run_worker,
                                            name="SiegfriedFocusWriter", daemon=True)
            self._thread = (threading.Thread(target=self._run_loop,
                                            name="SiegfriedFocusDBusAdapter", daemon=True)
                            if self._owns_loop else None)
            self._worker.start()
            if self._thread is not None:
                self._thread.start()
            return True
        except Exception:
            logger.info("Optional focus adapter unavailable; core remains operational")
            self.stop(discard=True)
            return False

    def _disconnected(self, connection: Any) -> None:
        # A deferred notification from a closed old bus must not stop a restart.
        if connection is self._bus:
            self.stop(discard=True)

    def _owner_changed(self, name: str, old: str, new: str) -> None:
        if str(new) != self._kwin_owner:
            self.stop(discard=True)

    def _run_loop(self) -> None:
        try:
            self._loop.run()
        finally:
            if self._running:
                self.stop(discard=True)

    def stop(self, discard: bool = False, *, close_tracker: bool = True) -> bool:
        """Release even partial startup; report surviving threads honestly."""
        self._kwin_owner = None
        with self._condition:
            self._running = False
            if discard:
                self._pending.clear()
                self._generation += 1
            self._condition.notify_all()
        clean = True
        for resource, method in ((self._owner_match, "remove"),
                                 (self._service_object, "remove_from_connection")):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception:
                    clean = False
        self._owner_match = self._service_object = None
        bus, self._bus = self._bus, None
        if bus is not None:
            try:
                if self._bus_name is not None:
                    bus.release_name(self.DBUS_SERVICE_NAME)
                    self._bus_name = None  # Finalizer must run before connection closes.
                bus.close()
            except Exception:
                clean = False
        self._bus_name = self._bus = None
        if self._loop is not None and self._owns_loop:
            self._loop.quit()
        for thread in (self._thread, self._worker):
            if (thread is not None and thread is not threading.current_thread()
                    and thread.is_alive()):
                thread.join(timeout=1.0)
                clean = clean and not thread.is_alive()
        if close_tracker and (self._worker is None or not self._worker.is_alive()):
            if discard:
                self.tracker.on_resume()
            else:
                try:
                    self.tracker.close_active_interval()
                except Exception:
                    clean = False
        return clean

    def is_running(self) -> bool:
        return self._running
