#!/usr/bin/env python3
"""boot_hook.py

Hook matutino de inicio de sesión para KDE Plasma.
Calcula la ventana estimada de descanso heurística:
ventana_descanso_estimada = (Hora Actual - Hora Apagado/Suspensión) - 25 min (latencia conciliación)
"""

import sys
import time
from pathlib import Path

# Add src to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from siegfried.core.rest import ClockSample, RestEstimate, estimate_rest


def calculate_estimated_rest_window(last_shutdown_epoch: float | None,
                                    now_epoch: float | None = None, *,
                                    interval_verified: bool = False) -> RestEstimate:
    """Pure bridge to the estimator. Wall timestamps alone are insufficient.

    The caller must independently verify the shutdown/return pair and clock
    continuity. No shutdown time is fabricated, no system log is scraped, and
    no circadian event is written. Cross-boot inference stays explicit.
    """
    now = now_epoch if now_epoch is not None else time.time()
    start = ClockSample(last_shutdown_epoch) if last_shutdown_epoch is not None else None
    return estimate_rest(start, ClockSample(now), cause="shutdown",
                         continuous_evidence=interval_verified,
                         wall_clock_verified=interval_verified)


def main() -> None:
    from siegfried.integrations.notifications import DesktopNotificationSender

    # Existing greeting only; no Boot Briefing F5.3 is implemented here.
    notifier = DesktopNotificationSender()
    notifier.send(
        title="Siegfried — Sesión Iniciada",
        message="Buenos días, Señor. Sistema operativo listo.",
        urgency="normal"
    )


if __name__ == "__main__":
    main()
