#!/usr/bin/env python3
"""F5.3 measurements separate from historical SLO harness; no host notifications."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO/'src'))
from siegfried.core.briefing import BriefingContext, compose_briefing
from siegfried.observability.benchmarks import calculate_latency_stats


def stats(values):
    result=calculate_latency_stats(values)
    return {'count':result.count,'mean_ms':result.mean_ms,'p50_ms':result.p50_ms,
            'p95_ms':result.p95_ms,'max_ms':result.max_ms}


def main():
    values=[];ctx=BriefingContext(now=datetime(2026,10,9,8))
    for _ in range(1000):
        start=time.perf_counter();compose_briefing(ctx);values.append((time.perf_counter()-start)*1000)
    cold=[]
    for _ in range(25):
        start=time.perf_counter()
        subprocess.run([sys.executable,'-S',str(REPO/'scripts/boot_hook.py'),'--dry-run'],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=2)
        cold.append((time.perf_counter()-start)*1000)
    print(json.dumps({'pure_composition':stats(values),'hook_cold_start_dry_run':stats(cold),
                     'notification_visible_ms':None,'note':'Dry-run is not KDE delivery or visual appearance.'},indent=2))
    return 0


if __name__=='__main__':sys.exit(main())
