"""Integrations package exposing notification, audio and KWin interfaces."""

from siegfried.integrations.notifications import (
    NotificationSender,
    DesktopNotificationSender,
    StubNotificationSender,
)
from siegfried.integrations.audio import (
    AudioPlayer,
    PipeWireAudioPlayer,
    StubAudioPlayer,
)
from siegfried.integrations.kwin import (
    SanitizedWindowFocus,
    sanitize_window_event,
)

__all__ = [
    "NotificationSender",
    "DesktopNotificationSender",
    "StubNotificationSender",
    "AudioPlayer",
    "PipeWireAudioPlayer",
    "StubAudioPlayer",
    "SanitizedWindowFocus",
    "sanitize_window_event",
]
