"""Pure, deterministic briefing composition. No desktop, filesystem or inference."""
from dataclasses import dataclass
from datetime import datetime
import math
import re
import unicodedata

from siegfried.core.rest import RestEstimate, RestStatus


def screen_text(value, limit=100):
    """Reject private-looking/control/markup input rather than quote it on screen."""
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
        return None
    value = value.strip()
    if any(unicodedata.category(c).startswith('C') for c in value):
        return None
    if re.search(r'(?i)(https?://|www\.|sk-|bearer\s|[/\\<>`$]|[;&|])', value):
        return None
    return value


@dataclass(frozen=True)
class BriefingContext:
    now: datetime
    available: bool = False
    title: str = 'Señor'
    critical_task: str | None = None
    expose_task: bool = False
    unlocked: bool = False
    rest: RestEstimate | None = None
    weather: str | None = None


def compose_briefing(context: BriefingContext) -> str:
    if not isinstance(context, BriefingContext) or not isinstance(context.now, datetime):
        raise ValueError('invalid_briefing_context')
    hour = context.now.hour
    greeting = 'Buenos días' if 5 <= hour < 12 else 'Buenas tardes' if 12 <= hour < 19 else 'Buenas noches'
    title = context.title if context.title in ('Señor', 'Joven') else 'Señor'
    parts = [f'{greeting}, {title}.',
             'Siegfried está preparado.' if context.available is True else
             'La consola de Siegfried está disponible; el daemon no está confirmado.']
    if context.unlocked is True:
        task = screen_text(context.critical_task)
        if context.expose_task is True and task:
            parts.append(f'Tarea prioritaria: {task}.')
        rest = context.rest
        if isinstance(rest, RestEstimate) and rest.status is RestStatus.ESTIMATED:
            try:
                valid = (type(rest.elapsed_seconds) in (int, float) and math.isfinite(rest.elapsed_seconds)
                         and rest.elapsed_seconds >= 5400 and type(rest.estimated_minutes) in (int, float)
                         and math.isfinite(rest.estimated_minutes)
                         and abs(rest.estimated_minutes - (rest.elapsed_seconds / 60 - 25)) < .001)
            except (OverflowError, ValueError):
                valid = False
            if valid:
                minutes = int(rest.estimated_minutes)
                parts.append(f'La ventana estimada de descanso es de {minutes // 60} h {minutes % 60} min.')
    weather = screen_text(context.weather, 120)
    if weather:
        parts.append(f'Clima en Lima: {weather}.')
    return ' '.join(parts)


def context_from_session(controller, *, now, **kwargs):
    """In-process bridge only; never reconstruct evidence through new files/IPC."""
    snapshot = controller.snapshot()
    return BriefingContext(now=now, unlocked=snapshot['state'] == 'ACTIVE',
                           rest=controller.rest_estimate, **kwargs)
