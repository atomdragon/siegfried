"""Deterministic health and focus state machine.

Rule: The LLM never transitions this state. All transitions are validated here.
"""

from typing import Dict, Set
from siegfried.contracts.states import (
    SystemState,
    MAX_CONTINUOUS_SITTING_SECONDS,
    MAX_POSTPONE_SECONDS,
)
from siegfried.core.errors import InvalidStateTransitionError, PostureLimitReachedError


# Allowed direct state transitions
VALID_TRANSITIONS: Dict[SystemState, Set[SystemState]] = {
    SystemState.IDLE: {
        SystemState.POMODORO_RUNNING,
    },
    SystemState.POMODORO_RUNNING: {
        SystemState.IDLE,                   # Cancelled or aborted
        SystemState.BREAK_RUNNING,          # Normal completion
        SystemState.POSTPONE_RUNNING,       # Flow postponement
        SystemState.CRITICAL_BREAK_REQUIRED,# Hit hard 60m posture barrier
    },
    SystemState.POSTPONE_RUNNING: {
        SystemState.IDLE,                   # Cancelled
        SystemState.BREAK_RUNNING,          # Postpone ended, into break
        SystemState.CRITICAL_BREAK_REQUIRED,# Postpone pushed over 60m
    },
    SystemState.BREAK_RUNNING: {
        SystemState.IDLE,                   # Break completed or interrupted
        SystemState.POMODORO_RUNNING,       # Started new block directly after break
    },
    SystemState.CRITICAL_BREAK_REQUIRED: {
        SystemState.BREAK_RUNNING,          # Acknowledged break commenced
        SystemState.IDLE,                   # Break fulfilled / reset
    },
}


class HealthStateMachine:
    """Manages system state and enforces posture constraints."""

    def __init__(self, initial_state: SystemState = SystemState.IDLE) -> None:
        self._state = initial_state
        self._continuous_sitting_seconds: float = 0.0

    @property
    def state(self) -> SystemState:
        return self._state

    @property
    def continuous_sitting_seconds(self) -> float:
        return self._continuous_sitting_seconds

    def add_sitting_time(self, seconds: float) -> None:
        """Accumulate sitting time and check against posture barrier."""
        if seconds < 0:
            return
        self._continuous_sitting_seconds += seconds
        if self._continuous_sitting_seconds >= MAX_CONTINUOUS_SITTING_SECONDS:
            if self._state not in (SystemState.BREAK_RUNNING, SystemState.IDLE):
                self._state = SystemState.CRITICAL_BREAK_REQUIRED

    def reset_sitting_time(self) -> None:
        """Reset sitting counter when user stands up and completes break."""
        self._continuous_sitting_seconds = 0.0

    def transition_to(self, target_state: SystemState, reason: str = "") -> None:
        """Transition safely or raise InvalidStateTransitionError."""
        if target_state == self._state:
            return

        # Check posture barrier
        if self._continuous_sitting_seconds >= MAX_CONTINUOUS_SITTING_SECONDS:
            if target_state in (SystemState.POMODORO_RUNNING, SystemState.POSTPONE_RUNNING):
                raise PostureLimitReachedError(
                    f"Posture hard limit of {int(MAX_CONTINUOUS_SITTING_SECONDS // 60)} minutes reached. "
                    "Cannot start or postpone focus until a physical break is taken."
                )

        allowed = VALID_TRANSITIONS.get(self._state, set())
        if target_state not in allowed:
            raise InvalidStateTransitionError(
                from_state=self._state.value,
                to_state=target_state.value,
                reason=reason or f"Transition not allowed from {self._state.value}"
            )

        self._state = target_state

    def can_postpone(self, requested_seconds: float) -> bool:
        """Check if postponing is permitted under posture rules."""
        if self._state != SystemState.POMODORO_RUNNING:
            return False
        if requested_seconds > MAX_POSTPONE_SECONDS:
            return False
        if (self._continuous_sitting_seconds + requested_seconds) > MAX_CONTINUOUS_SITTING_SECONDS:
            return False
        return True
