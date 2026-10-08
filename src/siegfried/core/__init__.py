"""Core package exposing deterministic state machine and clock abstractions."""

from siegfried.core.errors import (
    SiegfriedError,
    InvalidStateTransitionError,
    PostureLimitReachedError,
    StorageError,
    IPCCommunicationError,
)
from siegfried.core.clock import Clock, SystemClock
from siegfried.core.state_machine import HealthStateMachine, VALID_TRANSITIONS

__all__ = [
    "SiegfriedError",
    "InvalidStateTransitionError",
    "PostureLimitReachedError",
    "StorageError",
    "IPCCommunicationError",
    "Clock",
    "SystemClock",
    "HealthStateMachine",
    "VALID_TRANSITIONS",
]
