"""Contracts package exposing schemas and protocols for Siegfried."""

from siegfried.contracts.states import (
    SystemState,
    MAX_CONTINUOUS_SITTING_SECONDS,
    POSTURE_WARNING_SECONDS,
    MAX_POSTPONE_SECONDS,
)
from siegfried.contracts.events import (
    Event,
    EventType,
    EVENT_SCHEMA_VERSION,
)
from siegfried.contracts.ipc import (
    IPCRequest,
    IPCResponse,
    IPCCommand,
    IPCStatus,
    IPC_PROTOCOL_VERSION,
)
from siegfried.contracts.config import (
    CONFIG_SCHEMA_VERSION,
    get_default_core_profile,
    get_default_active_agenda,
    validate_core_profile,
    validate_active_agenda,
)
from siegfried.contracts.inference import (
    INFERENCE_CONTRACT_VERSION,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    InferenceUsage,
)

__all__ = [
    "SystemState",
    "MAX_CONTINUOUS_SITTING_SECONDS",
    "POSTURE_WARNING_SECONDS",
    "MAX_POSTPONE_SECONDS",
    "Event",
    "EventType",
    "EVENT_SCHEMA_VERSION",
    "IPCRequest",
    "IPCResponse",
    "IPCCommand",
    "IPCStatus",
    "IPC_PROTOCOL_VERSION",
    "CONFIG_SCHEMA_VERSION",
    "get_default_core_profile",
    "get_default_active_agenda",
    "validate_core_profile",
    "validate_active_agenda",
    "INFERENCE_CONTRACT_VERSION",
    "InferenceMessage",
    "InferenceRequest",
    "InferenceResponse",
    "InferenceUsage",
]

