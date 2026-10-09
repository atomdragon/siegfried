"""Desktop notifications abstraction for KDE Plasma via notify-send / kdialog.

RULE: Domain code never calls subprocess directly for notifications.
All desktop alerts flow through NotificationSender implementations.
"""

import os
import re
import shutil
import subprocess
import time
from typing import Optional, Protocol

from siegfried.contracts.alerts import AlertUrgency, NotificationAttempt


def _sanitize_alert_text(text: str) -> str:
    """Sanitize notification content to ensure zero leakage of tokens or secrets."""
    cleaned = re.sub(r"sk-[a-zA-Z0-9_\-]+", "[REDACTED_KEY]", str(text))
    cleaned = re.sub(r"Bearer\s+[^\s,]+", "Bearer [REDACTED_TOKEN]", cleaned)
    return cleaned


class NotificationSender(Protocol):
    """Protocol for sending desktop alerts."""

    def send(self, title: str, message: str, urgency: str = "normal") -> bool:
        ...


class DesktopNotificationSender:
    """Linux desktop notification sender targeting org.freedesktop.Notifications via notify-send."""

    def __init__(self, executable: Optional[str] = None) -> None:
        self._notify_send_cmd = executable or shutil.which("notify-send")
        self.last_attempt: Optional[NotificationAttempt] = None

    def has_session(self) -> bool:
        """Check whether a desktop display or D-Bus session bus is available."""
        return bool(
            os.environ.get("WAYLAND_DISPLAY")
            or os.environ.get("DISPLAY")
            or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        )

    def is_available(self) -> bool:
        """Check if notify-send is installed and desktop session is reachable."""
        return bool(self._notify_send_cmd) and self.has_session()

    def send(self, title: str, message: str, urgency: str = "normal") -> bool:
        """Send notification without shell=True, with strict validation and timeout."""
        # 1. Validate urgency against allowed values
        valid_urgencies = {AlertUrgency.LOW.value, AlertUrgency.NORMAL.value, AlertUrgency.CRITICAL.value}
        urgency_val = urgency if urgency in valid_urgencies else AlertUrgency.NORMAL.value

        # 2. Check session and executable availability
        if not self._notify_send_cmd:
            self.last_attempt = NotificationAttempt(
                alert_id="",
                attempted_at=time.time(),
                accepted=False,
                error_msg="notify-send binary not found on system PATH"
            )
            return False

        if not self.has_session():
            self.last_attempt = NotificationAttempt(
                alert_id="",
                attempted_at=time.time(),
                accepted=False,
                error_msg="No active desktop session detected (missing DISPLAY, WAYLAND_DISPLAY, or DBUS_SESSION_BUS_ADDRESS)"
            )
            return False

        # 3. Sanitize inputs
        clean_title = _sanitize_alert_text(title)
        clean_msg = _sanitize_alert_text(message)

        # 4. Execute external notify-send strictly without shell=True
        cmd = [self._notify_send_cmd, "-u", urgency_val, "-a", "Siegfried", clean_title, clean_msg]
        try:
            res = subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0
            )
            accepted = (res.returncode == 0)
            self.last_attempt = NotificationAttempt(
                alert_id="",
                attempted_at=time.time(),
                accepted=accepted,
                error_msg=None if accepted else f"notify-send returned exit code {res.returncode}"
            )
            return accepted
        except subprocess.TimeoutExpired:
            self.last_attempt = NotificationAttempt(
                alert_id="",
                attempted_at=time.time(),
                accepted=False,
                error_msg="notify-send timed out after 2.0s"
            )
            return False
        except (subprocess.SubprocessError, OSError) as e:
            self.last_attempt = NotificationAttempt(
                alert_id="",
                attempted_at=time.time(),
                accepted=False,
                error_msg=f"Subprocess invocation failure: {e}"
            )
            return False


class StubNotificationSender:
    """Test stub capturing notifications in memory for hermetic testing."""

    def __init__(self) -> None:
        self.sent_notifications: list[tuple[str, str, str]] = []
        self.attempts: list[NotificationAttempt] = []

    def send(self, title: str, message: str, urgency: str = "normal") -> bool:
        clean_title = _sanitize_alert_text(title)
        clean_msg = _sanitize_alert_text(message)
        self.sent_notifications.append((clean_title, clean_msg, urgency))
        self.attempts.append(NotificationAttempt(
            alert_id="",
            attempted_at=time.time(),
            accepted=True,
            error_msg=None
        ))
        return True
