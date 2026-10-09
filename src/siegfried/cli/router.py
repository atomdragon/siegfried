"""Fast-Path Regex Router.

Target SLO: P95 <10 ms latency.
Direct intents are resolved locally without consulting the LLM.
"""

import re
from typing import NamedTuple, Optional, Dict, Any
from siegfried.contracts.ipc import IPCCommand


class RouteMatch(NamedTuple):
    is_fast_path: bool
    command: Optional[IPCCommand]
    args: Dict[str, Any]
    direct_response: Optional[str] = None


class CommandRouter:
    """Regex pattern matcher for direct operational commands."""

    def __init__(self) -> None:
        # Precompile regex rules for sub-millisecond dispatch
        self._patterns = [
            # Iniciar pomodoro / bloque: "iniciar bloque de 50", "bloque 25", "pomodoro 50"
            (
                re.compile(r"^(?:iniciar\s+)?(?:bloque|pomodoro)(?:\s+de)?\s+(\d+)(?:\s+min)?(?:\s+(?:(?:para|en)\s+)?(.+))?$", re.IGNORECASE),
                self._match_start_focus
            ),
            # "status", "estado", "tiempo", "¿cuánto tiempo me queda?"
            (
                re.compile(r"^(?:¿)?(?:status|estado|tiempo|cu[aá]nto\s+(?:tiempo\s+)?(?:me\s+)?(?:falta|queda))(?:\?)?$", re.IGNORECASE),
                lambda m: RouteMatch(True, IPCCommand.STATUS, {})
            ),

            # "cancelar", "detener", "abortar"
            (
                re.compile(r"^(?:cancelar|detener|abortar|parar)(?:\s+(?:bloque|pomodoro))?$", re.IGNORECASE),
                lambda m: RouteMatch(True, IPCCommand.CANCEL_FOCUS, {})
            ),
            # "silencio", "pausa", "ack", "descanso"
            (
                re.compile(r"^(?:silencio|parar\s+alarma|ack|descanso)$", re.IGNORECASE),
                lambda m: RouteMatch(True, IPCCommand.ACK_BREAK, {})
            ),
            # "ping"
            (
                re.compile(r"^(?:¿)?ping(?:\?)?$", re.IGNORECASE),
                lambda m: RouteMatch(True, IPCCommand.PING, {})
            ),
            # "ayuda", "help", "?"
            (
                re.compile(r"^(?:¿)?(?:ayuda|help|\?)(?:\?)?$", re.IGNORECASE),
                lambda m: RouteMatch(
                    True,
                    None,
                    {},
                    direct_response=(
                        "Comandos deterministas disponibles:\n"
                        "  • estado / status / tiempo     — Ver tiempo restante y postura\n"
                        "  • bloque [min] [para <tarea>]  — Iniciar bloque de enfoque (ej. 'bloque 25')\n"
                        "  • posponer [min]               — Solicitar prórroga (máx 60 min continuo)\n"
                        "  • cancelar / detener           — Cancelar bloque de enfoque actual\n"
                        "  • silencio / descanso / ack    — Silenciar alarma e iniciar descanso activo\n"
                        "  • ping                         — Probar conexión con el daemon\n"
                        "  • salir / exit / quit          — Finalizar sesión interactiva\n\n"
                        "Cualquier otra consulta se procesará como pregunta cognitiva (IA)."
                    )
                )
            ),
            # "posponer 10", "proorrogar 5"
            (
                re.compile(r"^(?:posponer|proorrogar)\s+(\d+)$", re.IGNORECASE),
                self._match_postpone
            ),
        ]

        # Historical query patterns (C1)
        # Week window queries
        self._historical_week_patterns = [
            re.compile(
                r"^(?:¿)?(?:.*?\b)?(?:resumen|trabaj[eé]|hice|tiempo|horas|pomodoros|pausas|m[eé]tricas).*\b(?:esta\s+semana|de\s+la\s+semana|semanal|últimos?\s+7\s+d[ií]as)(?:\?)?$",
                re.IGNORECASE,
            ),
            re.compile(
                r"^(?:¿)?(?:resumen\s+de\s+(?:la\s+semana|esta\s+semana)|resumen\s+semanal)(?:\?)?$",
                re.IGNORECASE,
            ),
            re.compile(
                r"^(?:¿)?(?:.*?\b)?(?:últimos?\s+7\s+d[ií]as)(?:\?)?$",
                re.IGNORECASE,
            ),
        ]
        # Today window queries (and general historical queries defaulting to today)
        self._historical_today_patterns = [
            re.compile(
                r"^(?:¿)?(?:.*?\b)?(?:resumen|trabaj[eé]|hice|tiempo|horas|pomodoros|pausas|pr[oó]rrogas|m[eé]tricas|llevo).*\b(?:hoy|del\s+d[ií]a|diario)(?:\?)?$",
                re.IGNORECASE,
            ),
            re.compile(
                r"^(?:¿)?(?:cu[aá]nt[ao]s?\s+(?:horas|tiempo|pomodoros|pausas|pr[oó]rrogas)\s+(?:trabaj[eé]|hice|llevo|he\s+hecho|he\s+trabajado))(?:\?)?$",
                re.IGNORECASE,
            ),
            re.compile(
                r"^(?:¿)?(?:resumen\s+de\s+(?:productividad|enfoque|trabajo)|mis\s+m[eé]tricas|mi\s+historial)(?:\?)?$",
                re.IGNORECASE,
            ),
        ]

    def _match_start_focus(self, match: re.Match) -> RouteMatch:
        duration_min = int(match.group(1))
        task = match.group(2) if match.lastindex >= 2 and match.group(2) else "Bloque de trabajo"
        clean_task = task.strip()
        if clean_task.lower().startswith("para "):
            clean_task = clean_task[5:].strip()
        elif clean_task.lower().startswith("en "):
            clean_task = clean_task[3:].strip()
        return RouteMatch(
            is_fast_path=True,
            command=IPCCommand.START_FOCUS,
            args={"duration_min": duration_min, "task": clean_task or "Bloque de trabajo"}
        )

    def _match_postpone(self, match: re.Match) -> RouteMatch:
        minutes = int(match.group(1))
        return RouteMatch(
            is_fast_path=True,
            command=IPCCommand.POSTPONE,
            args={"minutes": minutes}
        )

    def route(self, user_input: str) -> RouteMatch:
        """Route user query via Fast-Path regex, Historical Cognitive path, or general LLM path."""
        cleaned = user_input.strip()
        if not cleaned:
            return RouteMatch(is_fast_path=True, command=None, args={}, direct_response="")

        # Check precompiled fast-path patterns
        for pattern, handler in self._patterns:
            m = pattern.match(cleaned)
            if m:
                return handler(m)

        # Check historical patterns (Cognitive path with deterministic context extraction)
        for pattern in self._historical_week_patterns:
            if pattern.search(cleaned):
                return RouteMatch(
                    is_fast_path=False,
                    command=None,
                    args={
                        "raw_text": cleaned,
                        "is_historical": True,
                        "time_window": "week",
                    },
                )

        for pattern in self._historical_today_patterns:
            if pattern.search(cleaned):
                return RouteMatch(
                    is_fast_path=False,
                    command=None,
                    args={
                        "raw_text": cleaned,
                        "is_historical": True,
                        "time_window": "today",
                    },
                )

        # Longer or general conversational queries fall back to cognitive path
        return RouteMatch(
            is_fast_path=False,
            command=None,
            args={
                "raw_text": cleaned,
                "is_historical": False,
            },
        )
