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
                re.compile(r"^(?:iniciar\s+)?(?:bloque|pomodoro)(?:\s+de)?\s+(\d+)(?:\s+min)?(?:\s+(?:para|en)\s+(.+))?$", re.IGNORECASE),
                self._match_start_focus
            ),
            # "status", "estado", "tiempo"
            (
                re.compile(r"^(?:status|estado|tiempo|cuanto\s+falta)$", re.IGNORECASE),
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
            # "posponer 10", "proorrogar 5"
            (
                re.compile(r"^(?:posponer|proorrogar)\s+(\d+)$", re.IGNORECASE),
                self._match_postpone
            ),
        ]

    def _match_start_focus(self, match: re.Match) -> RouteMatch:
        duration_min = int(match.group(1))
        task = match.group(2) if match.lastindex >= 2 and match.group(2) else "Bloque de trabajo"
        return RouteMatch(
            is_fast_path=True,
            command=IPCCommand.START_FOCUS,
            args={"duration_min": duration_min, "task": task.strip()}
        )

    def _match_postpone(self, match: re.Match) -> RouteMatch:
        minutes = int(match.group(1))
        return RouteMatch(
            is_fast_path=True,
            command=IPCCommand.POSTPONE,
            args={"minutes": minutes}
        )

    def route(self, user_input: str) -> RouteMatch:
        """Route user query via Fast-Path regex or flag for Cognitive Deep-Path (LLM)."""
        cleaned = user_input.strip()
        if not cleaned:
            return RouteMatch(is_fast_path=True, command=None, args={}, direct_response="")

        # Check precompiled patterns
        for pattern, handler in self._patterns:
            m = pattern.match(cleaned)
            if m:
                return handler(m)

        # Longer or conversational queries fall back to cognitive path
        return RouteMatch(is_fast_path=False, command=None, args={"raw_text": cleaned})
