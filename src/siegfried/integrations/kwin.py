"""KWin window tracking interface and privacy sanitizer.

RULES:
1. Event-driven via KWin script (no polling).
2. Store ONLY application name, category, and duration.
3. NEVER store full window titles, URLs, or sensitive data.
"""

from typing import Dict, Any, NamedTuple


class SanitizedWindowFocus(NamedTuple):
    application: str
    category: str
    duration_seconds: float

    def to_event_data(self) -> Dict[str, Any]:
        return {
            "application": self.application,
            "category": self.category,
            "duration_seconds": round(self.duration_seconds, 2)
        }


# Basic heuristic categories (customizable via profile)
CATEGORY_MAPPINGS: Dict[str, str] = {
    "code": "development",
    "codium": "development",
    "konsole": "terminal",
    "alacritty": "terminal",
    "kitty": "terminal",
    "firefox": "browser",
    "google-chrome": "browser",
    "chromium": "browser",
    "discord": "communication",
    "slack": "communication",
    "telegram": "communication",
    "spotify": "media",
}


def sanitize_window_event(app_name: str, duration_seconds: float) -> SanitizedWindowFocus:
    """Sanitize and classify a window focus event."""
    clean_app = (app_name or "unknown").strip().lower()
    # Strip any trailing path or parameters
    if "/" in clean_app:
        clean_app = clean_app.split("/")[-1]
    category = CATEGORY_MAPPINGS.get(clean_app, "other")
    return SanitizedWindowFocus(
        application=clean_app,
        category=category,
        duration_seconds=max(0.0, float(duration_seconds))
    )
