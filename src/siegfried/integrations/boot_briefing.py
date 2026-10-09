"""Short-lived boot coordinator. First notification precedes optional weather."""
from dataclasses import replace
from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

from siegfried.core.briefing import BriefingContext, compose_briefing
from siegfried.core.rest import RestEstimate, RestStatus
from siegfried.integrations.briefing_files import BriefingLease, read_critical_task, session_key
from siegfried.integrations.briefing_kde import KDEBriefingPresenter, REPLLauncher, fallback_notification

logger = logging.getLogger(__name__)


def bounded_worker(repository, arguments, timeout):
    """Hard process deadline also bounds blocking libc DNS; no lingering workers."""
    child = None
    try:
        child = subprocess.Popen([sys.executable, '-S', str(Path(repository) / 'scripts/boot_hook.py'), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'}, close_fds=True)
        output, _ = child.communicate(timeout=timeout)
        return output[:1024] if child.returncode == 0 and len(output) <= 1024 else b''
    except (OSError, subprocess.TimeoutExpired):
        return b''
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.communicate(timeout=.1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()
            if child.stdout:
                child.stdout.close()


def run_briefing(paths, repository, *, weather=False, show_task=False, action_window=12,
                 presenter=None, context=None, dry_run=False):
    start = time.monotonic()
    context = context or BriefingContext(now=datetime.now())
    generic = replace(context, critical_task=None, expose_task=False, unlocked=False, rest=None, weather=None)
    if dry_run:
        print(compose_briefing(generic))
        return {'dry_run':True}
    if not isinstance(action_window, (int, float)) or not .01 <= action_window <= 30:
        raise ValueError('invalid_action_window')
    presenter = presenter or KDEBriefingPresenter(on_open=REPLLauncher(repository)
        if REPLLauncher(repository).available() else None)
    try:
        with BriefingLease(paths, session_key()) as lease:
            if lease.duplicate:
                return {'duplicate':True, 'total_ms':(time.monotonic()-start)*1000}
            native = presenter.start()
            try:
                available = bounded_worker(repository, ['--probe-worker', '--socket-path', str(paths.socket_file)], .15) == b'READY\n'
                generic = replace(generic, available=available)
                context = replace(context, available=available, unlocked=native and presenter.unlocked,
                                  expose_task=show_task is True,
                                  critical_task=read_critical_task(paths) if show_task and native and presenter.unlocked else None)
                if isinstance(context.rest, RestEstimate) and context.rest.status == RestStatus.INVALID_INTERVAL:
                    logger.info('briefing_rest_invalid_interval')
                composed_at = time.monotonic()
                text, fallback = compose_briefing(context), compose_briefing(generic)
                composition_ms = (time.monotonic()-composed_at)*1000
                sent = presenter.send(text, fallback, expiry_ms=int(action_window*1000)) if native else fallback_notification(fallback)
                sent_ms = (time.monotonic()-start)*1000
                if sent:
                    lease.mark_sent()
                weather_ms = None
                if weather and sent and native:
                    from siegfried.integrations.weather import parse_weather
                    weather_start = time.monotonic()
                    raw = bounded_worker(repository, ['--weather-worker'], .8)
                    result = parse_weather(raw)
                    weather_ms = (time.monotonic()-weather_start)*1000
                    presenter.dispatch_pending()
                    remaining = action_window - (time.monotonic()-start)
                    if result and remaining > 0 and presenter.notification_id is not None and not presenter.opened and not presenter.disconnected:
                        context = replace(context, weather=result)
                        generic = replace(generic, weather=result)
                        # Replaces our notification ID, never creates a second briefing.
                        presenter.send(compose_briefing(context), compose_briefing(generic),
                                       expiry_ms=max(1,int(remaining*1000)))
                if native and sent and presenter.actions:
                    remaining = action_window - (time.monotonic()-start)
                    if remaining > 0:
                        presenter.wait(remaining)
                elif native and sent:
                    # Passive notification has no callback owner to retain; the
                    # server owns its expiry. Do not dismiss it immediately.
                    presenter.notification_id = None
                return {'native':native, 'sent':sent, 'actions':native and presenter.actions,
                        'composition_ms':composition_ms, 'notification_sent_ms':sent_ms,
                        'weather_ms':weather_ms, 'total_ms':(time.monotonic()-start)*1000,
                        'visible_ms':None, 'inhibited':presenter.inhibited if native else None}
            finally:
                presenter.close()
    except (OSError, ValueError):
        logger.warning('briefing_runtime_unavailable')
        print(compose_briefing(generic))
        return {'sent':False, 'runtime_unavailable':True, 'total_ms':(time.monotonic()-start)*1000}
