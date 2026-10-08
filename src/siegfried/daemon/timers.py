"""Daemon timers and deadline tracking using monotonic clock.

CRITICAL RULE:
Never use time.sleep() for countdowns.
Deadlines are calculated as `time.monotonic() + duration`.
"""

import time
from typing import NamedTuple, Callable


class TimerSnapshot(NamedTuple):
    is_active: bool
    remaining_seconds: float
    total_duration_seconds: float
    task_name: str


class MonotonicTimer:
    """Manages an active focus countdown based strictly on time.monotonic()."""

    def __init__(self, on_expire: Callable[[], None] | None = None) -> None:
        self.on_expire = on_expire
        self._target_monotonic: float | None = None
        self._total_duration: float = 0.0
        self._task_name: str = ""
        self._has_expired: bool = False

    def start(self, duration_seconds: float, task_name: str = "") -> None:
        """Arm timer for specified duration."""
        self._total_duration = float(duration_seconds)
        self._target_monotonic = time.monotonic() + self._total_duration
        self._task_name = task_name
        self._has_expired = False

    def cancel(self) -> None:
        """Cancel and disarm timer."""
        self._target_monotonic = None
        self._total_duration = 0.0
        self._task_name = ""
        self._has_expired = False

    def is_active(self) -> bool:
        """Check if timer is currently running."""
        return self._target_monotonic is not None and not self._has_expired

    def remaining_seconds(self) -> float:
        """Remaining duration in seconds."""
        if not self.is_active() or self._target_monotonic is None:
            return 0.0
        rem = self._target_monotonic - time.monotonic()
        return max(0.0, rem)

    def check_expiration(self) -> bool:
        """Poll to check if deadline has elapsed. Triggers callback once."""
        if not self.is_active() or self._target_monotonic is None:
            return False

        if time.monotonic() >= self._target_monotonic:
            self._has_expired = True
            if self.on_expire:
                self.on_expire()
            return True
        return False

    def snapshot(self) -> TimerSnapshot:
        """Get current timer state."""
        return TimerSnapshot(
            is_active=self.is_active(),
            remaining_seconds=round(self.remaining_seconds(), 2),
            total_duration_seconds=self._total_duration,
            task_name=self._task_name
        )
