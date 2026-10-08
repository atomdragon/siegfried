"""Event Schema v1 definition and serialization."""

from enum import Enum
from typing import Any, Dict, NamedTuple
import time


class EventType(str, Enum):
    """Catalog of formal events recorded in siegfried_vault.jsonl."""
    # Focus and work cycles
    POMODORO_STARTED = "pomodoro_started"
    POMODORO_COMPLETED = "pomodoro_completed"
    POMODORO_CANCELLED = "pomodoro_cancelled"
    
    # Breaks and posture
    BREAK_STARTED = "break_started"
    BREAK_COMPLETED = "break_completed"
    BREAK_INTERRUPTED = "break_interrupted"
    POSTURE_WARNING = "posture_warning"
    POSTURE_LIMIT_REACHED = "posture_limit_reached"
    POSTPONE_GRANTED = "postpone_granted"
    POSTPONE_REJECTED = "postpone_rejected"
    
    # Circadian and desktop activity
    SLEEP_INITIATED = "sleep_initiated"
    WAKE_DETECTED = "wake_detected"
    WINDOW_FOCUS_SAMPLED = "window_focus_sampled"


EVENT_SCHEMA_VERSION: int = 1


class Event(NamedTuple):
    """Immutable representation of a Vault event conforming to Event Schema v1."""
    v: int
    ts: float
    type: str
    data: Dict[str, Any]

    @classmethod
    def create(cls, event_type: EventType | str, data: Dict[str, Any], ts: float | None = None) -> "Event":
        """Factory creating a validated Event Schema v1 instance."""
        t_str = event_type.value if isinstance(event_type, EventType) else str(event_type)
        return cls(
            v=EVENT_SCHEMA_VERSION,
            ts=float(ts if ts is not None else time.time()),
            type=t_str,
            data=dict(data)
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary matching Event Schema v1."""
        return {
            "v": self.v,
            "ts": self.ts,
            "type": self.type,
            "data": self.data
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        """Deserialize from dictionary, validating required fields."""
        if not isinstance(d, dict):
            raise ValueError(f"Event must be a JSON object, got {type(d)}")
        
        v = d.get("v")
        if v != EVENT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported event schema version: {v} (expected {EVENT_SCHEMA_VERSION})")
            
        ts = d.get("ts")
        if not isinstance(ts, (int, float)):
            raise ValueError(f"Event 'ts' must be a numeric timestamp, got {ts}")
            
        t_type = d.get("type")
        if not isinstance(t_type, str) or not t_type.strip():
            raise ValueError(f"Event 'type' must be a non-empty string, got {t_type}")
            
        data = d.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"Event 'data' must be an object, got {type(data)}")
            
        return cls(v=v, ts=float(ts), type=t_type, data=data)
