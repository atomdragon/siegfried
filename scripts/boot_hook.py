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


def main() -> int:
    import argparse
    import signal
    from siegfried.storage.paths import SiegfriedPaths

    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)

    parser = argparse.ArgumentParser(description='Briefing determinista de inicio KDE')
    parser.add_argument('--weather', action='store_true', help='Consulta HTTPS optativa de Lima')
    parser.add_argument('--show-task', action='store_true', help='Autoriza exponer nombre de tarea con pantalla desbloqueada')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--action-window', type=float, default=12.)
    parser.add_argument('--runtime-dir', type=Path)
    parser.add_argument('--weather-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--probe-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--socket-path', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.weather_worker:
        from siegfried.integrations.weather import fetch_weather
        result = fetch_weather()
        if result:
            print(result, end='')
        return 0
    if args.probe_worker:
        from siegfried.ipc.client import IPCClient
        from siegfried.contracts.ipc import IPCCommand, IPCStatus
        import os
        import stat
        try:
            st = args.socket_path.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                return 0
            response = IPCClient(args.socket_path, timeout_seconds=.1).call(IPCCommand.PING)
            if response.status == IPCStatus.OK.value and response.payload.get('pong') is True:
                print('READY')
        except Exception:
            pass  # Never print exception text, paths or daemon payloads.
        return 0
    from siegfried.integrations.boot_briefing import run_briefing
    result = run_briefing(SiegfriedPaths(runtime_dir=args.runtime_dir), REPO_ROOT,
                         weather=args.weather, show_task=args.show_task,
                         dry_run=args.dry_run, action_window=args.action_window)
    return 1 if result.get('runtime_unavailable') else 0


if __name__ == "__main__":
    sys.exit(main())
