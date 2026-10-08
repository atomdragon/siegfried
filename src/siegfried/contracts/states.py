"""Domain state definitions and health state machine states."""

from enum import Enum


class SystemState(str, Enum):
    """Core deterministic states for Siegfried health and focus cycle."""
    IDLE = "IDLE"
    POMODORO_RUNNING = "POMODORO_RUNNING"
    POSTPONE_RUNNING = "POSTPONE_RUNNING"
    BREAK_RUNNING = "BREAK_RUNNING"
    CRITICAL_BREAK_REQUIRED = "CRITICAL_BREAK_REQUIRED"


# Maximum continuous sitting time in seconds (hard posture limit: 60 minutes)
MAX_CONTINUOUS_SITTING_SECONDS: float = 3600.0

# Alert thresholds in seconds
POSTURE_WARNING_SECONDS: float = 3000.0  # 50 minutes warning

# Maximum postpone allowance in seconds (10 minutes)
MAX_POSTPONE_SECONDS: float = 600.0
