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

from siegfried.integrations.notifications import DesktopNotificationSender


def calculate_estimated_rest_window(last_shutdown_epoch: float, now_epoch: float | None = None) -> float:
    """Calcula la heurística de ventana de descanso en minutos."""
    now = now_epoch if now_epoch is not None else time.time()
    elapsed_minutes = (now - last_shutdown_epoch) / 60.0
    # Descuenta 25 minutos de latencia estimada de conciliación
    estimated = max(0.0, elapsed_minutes - 25.0)
    return estimated


def main() -> None:
    # Heurística inicial para boot hook
    notifier = DesktopNotificationSender()
    notifier.send(
        title="Siegfried — Sesión Iniciada",
        message="Buenos días, Señor. Sistema operativo listo.",
        urgency="normal"
    )


if __name__ == "__main__":
    main()
