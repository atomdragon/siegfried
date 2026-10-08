"""Monotonic and wall clock abstraction for testing and drift prevention.

CRITICAL RULE:
Never use time.sleep() for counting down intervals or measuring duration across laptop suspensions.
Use monotonic deadlines against time.monotonic().
"""

import time
from typing import Protocol


class Clock(Protocol):
    """Protocol for time measurement to facilitate testable determinism."""
    def now_wall(self) -> float:
        """Wall-clock epoch time in seconds."""
        ...

    def now_monotonic(self) -> float:
        """Monotonic time in seconds, immune to system clock adjustments."""
        ...


class SystemClock:
    """Standard system clock implementation."""
    def now_wall(self) -> float:
        return time.time()

    def now_monotonic(self) -> float:
        return time.monotonic()
