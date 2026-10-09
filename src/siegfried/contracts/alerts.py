"""Alert contracts and data representations for Gate F3.2.

Defines:
- AlertUrgency: Urgency level for desktop notifications (low, normal, critical).
- AlertType: Domain alert classifications.
- DeliveryStatus: Logical delivery attempt outcome.
- Alert: Immutable alert payload representation.
- NotificationAttempt: Audit record capturing desktop notification attempt.
"""

from enum import Enum
from pathlib import Path
from typing import NamedTuple, Optional
import time


class AlertUrgency(str, Enum):
    """Urgency level mapped to Freedesktop notification urgency."""
    LOW = "low"
    NORMAL = "normal"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """Catalog of sensory alerts triggered by deterministic domain events."""
    POMODORO_COMPLETED = "pomodoro_completed"
    POSTURE_WARNING = "posture_warning"
    POSTURE_LIMIT_REACHED = "posture_limit_reached"
    BREAK_STARTED = "break_started"
    POMODORO_CANCELLED = "pomodoro_cancelled"


class DeliveryStatus(str, Enum):
    """Logical status of desktop alert delivery attempt."""
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class Alert(NamedTuple):
    """Representation of an alert request conforming to F3.2 contract."""
    alert_id: str
    alert_type: AlertType
    title: str
    message: str
    urgency: str
    timestamp: float
    sound_path: Optional[Path] = None
    event_id: Optional[str] = None


class NotificationAttempt(NamedTuple):
    """Audit record capturing an attempted notification delivery."""
    alert_id: str
    attempted_at: float
    accepted: bool
    error_msg: Optional[str] = None
    status: Optional[str] = None
