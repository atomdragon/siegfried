"""Desktop notifications abstraction for KDE Plasma via notify-send / kdialog.

RULE: Domain code never calls subprocess directly for notifications.
"""

import shutil
import subprocess
from typing import Protocol


class NotificationSender(Protocol):
    """Protocol for sending desktop alerts."""
    def send(self, title: str, message: str, urgency: str = "normal") -> bool:
        ...


class DesktopNotificationSender:
    """Linux desktop notification sender using system notify-send."""

    def __init__(self) -> None:
        self._notify_send_cmd = shutil.which("notify-send")

    def send(self, title: str, message: str, urgency: str = "normal") -> bool:
        if not self._notify_send_cmd:
            return False
        try:
            subprocess.run(
                [self._notify_send_cmd, "-u", urgency, "-a", "Siegfried", title, message],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0
            )
            return True
        except (subprocess.SubprocessError, OSError):
            return False


class StubNotificationSender:
    """Test stub capturing notifications in memory."""

    def __init__(self) -> None:
        self.sent_notifications: list[tuple[str, str, str]] = []

    def send(self, title: str, message: str, urgency: str = "normal") -> bool:
        self.sent_notifications.append((title, message, urgency))
        return True
