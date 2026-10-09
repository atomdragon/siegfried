"""Standard-library session coordinator. FocusTracker remains the sole focus owner.

The native adapter serializes calls on its worker. The coordinator lock never
belongs to a bus callback; lock order is coordinator -> tracker, never inverse.
No session/rest events are persisted: frozen circadian events mean explicit
retirement/waking, not a logind suspend or a KDE screen lock.
"""
from enum import Enum
import threading
from typing import Optional

from siegfried.core.rest import ClockSample, RestEstimate, RestStatus, estimate_rest
from siegfried.daemon.focus import FocusTracker


class SessionState(str, Enum):
    UNKNOWN = "UNKNOWN"
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    SESSION_LOST = "SESSION_LOST"
    DEGRADED = "DEGRADED"
    STOPPED = "STOPPED"


class SessionController:
    KEYS = frozenset(("suspended", "shutting_down", "kde_locked", "locked_hint", "active"))

    def __init__(self, tracker: FocusTracker):
        self.tracker = tracker
        self._lock = threading.Lock()
        self._values = dict.fromkeys(self.KEYS)
        self._last_sample: Optional[ClockSample] = None
        self._suspend_start: Optional[ClockSample] = None
        self._state = SessionState.UNKNOWN
        self._enabled = False
        self._degraded = False
        self._lost = False
        self._stopped = False
        self.rest_estimate = RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="no_absence_pair")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def begin(self, sample: ClockSample) -> None:
        with self._lock:
            self._enabled = True
            self._stopped = self._lost = self._degraded = False
            self._values = dict.fromkeys(self.KEYS)
            self._suspend_start = None
            self._last_sample = sample
            self.rest_estimate = RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="daemon_restart")
            self._state = SessionState.UNKNOWN
            self.tracker.set_session_allowed(False, sample.monotonic, sample.wall, discard=True)

    def _reconcile_locked(self, sample: ClockSample, discard: bool = False, gate_ticket=None) -> None:
        v = self._values
        if self._stopped:
            state = SessionState.STOPPED
        elif self._lost:
            state = SessionState.SESSION_LOST
        elif v['shutting_down'] is True:
            state = SessionState.SHUTTING_DOWN
        elif self._degraded:
            state = SessionState.DEGRADED
        elif v['suspended'] is True:
            state = SessionState.SUSPENDED
        elif v['kde_locked'] is True or v['locked_hint'] is True:
            state = SessionState.LOCKED
        elif v['active'] is False:
            state = SessionState.INACTIVE
        elif any(value is None for value in v.values()):
            state = SessionState.UNKNOWN
        else:
            state = SessionState.ACTIVE
        self._state = state
        self.tracker.set_session_allowed(state == SessionState.ACTIVE,
                                         sample.monotonic, sample.wall, discard=discard, gate_ticket=gate_ticket)

    def initialize(self, values: dict, sample: ClockSample, gate_ticket=None) -> bool:
        """A startup snapshot is state evidence, never an observed absence onset."""
        with self._lock:
            if (not self._enabled or self._stopped or not isinstance(sample, ClockSample) or
                    not sample.valid() or sample.monotonic is None or not isinstance(values, dict) or
                    set(values) != self.KEYS or
                    any(value is not None and type(value) is not bool for value in values.values())):
                return False
            self._values = dict(values)
            self._last_sample = sample
            self._suspend_start = None
            self._reconcile_locked(sample, discard=True, gate_ticket=gate_ticket)
            return True

    def handle(self, kind: str, value: Optional[bool], sample: ClockSample, gate_ticket=None) -> bool:
        with self._lock:
            if not self._enabled or self._stopped:
                return False
            if not isinstance(sample, ClockSample) or not sample.valid() or sample.monotonic is None:
                return False
            if kind not in self.KEYS | {'degraded', 'session_lost'}:
                return False
            if kind in self.KEYS and type(value) is not bool:
                return False
            last = self._last_sample
            if last and (last.boot_id != sample.boot_id or
                         (last.monotonic is not None and sample.monotonic < last.monotonic)):
                self._degraded = True
                self._suspend_start = None
                self.rest_estimate = RestEstimate(RestStatus.INVALID_INTERVAL, reason="session_clock_disorder")
                self._reconcile_locked(sample, discard=True)
                return False
            self._last_sample = sample
            if kind == 'degraded':
                self._degraded = True
                self._suspend_start = None
                self.rest_estimate = RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="observation_gap")
            elif kind == 'session_lost':
                self._lost = True
                self._suspend_start = None
                self.rest_estimate = RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="session_lost")
            else:
                previous = self._values[kind]
                if previous is value:
                    return True
                self._values[kind] = value
                if kind == 'suspended':
                    if value:
                        self._suspend_start = sample if not self._degraded and not self._lost else None
                        self.rest_estimate = RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="absence_in_progress")
                    else:
                        self.rest_estimate = estimate_rest(self._suspend_start, sample, cause='suspend',
                                                          continuous_evidence=not self._degraded and not self._lost)
                        self._suspend_start = None
                elif kind == 'shutting_down' and value:
                    self._suspend_start = None
                    self.rest_estimate = RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="shutdown_unpaired")
            self._reconcile_locked(sample, discard=kind in ('degraded', 'session_lost'), gate_ticket=gate_ticket)
            return True

    def stop(self, sample: ClockSample) -> None:
        with self._lock:
            if self._stopped or not self._enabled:
                return
            self._stopped = True
            self._suspend_start = None
            self._reconcile_locked(sample)

    def snapshot(self) -> dict:
        with self._lock:
            return {'state': self._state.value, 'enabled': self._enabled,
                    'observability_degraded': self._degraded or any(v is None for v in self._values.values()),
                    'rest_status': self.rest_estimate.status.value,
                    'estimated_minutes': self.rest_estimate.estimated_minutes}
