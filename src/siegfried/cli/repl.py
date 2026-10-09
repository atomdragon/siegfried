"""CLI REPL loop and user interaction."""

import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from siegfried.contracts.inference import InferenceMessage
from siegfried.contracts.ipc import IPCCommand, IPCStatus
from siegfried.contracts.states import SystemState
from siegfried.core.errors import IPCCommunicationError
from siegfried.ipc.client import IPCClient
from siegfried.cli.router import CommandRouter
from siegfried.storage.paths import SiegfriedPaths, default_paths


class SiegfriedREPL:
    """Interactive command shell for Siegfried."""

    def __init__(
        self,
        client: IPCClient,
        paths: Optional[SiegfriedPaths] = None,
        max_context_turns: int = 6,
    ) -> None:
        self.client = client
        self.paths = paths or default_paths
        self.router = CommandRouter()
        self.max_context_turns = max_context_turns
        self._conversation_history: List[InferenceMessage] = []
        self._history_file: Path = self.paths.history_file
        self._has_readline = False

    @property
    def conversation_history(self) -> List[InferenceMessage]:
        """In-memory ephemeral conversation turns."""
        return list(self._conversation_history)

    def _setup_readline(self) -> None:
        """Initialize readline for command history with arrow navigation."""
        try:
            import readline
            self._has_readline = True
            # Privacy hardening: disable automatic history appending so cognitive prompts are not persisted
            if hasattr(readline, "set_auto_history"):
                readline.set_auto_history(False)
            readline.set_history_length(1000)
            if self._history_file.exists():
                try:
                    readline.read_history_file(str(self._history_file))
                except Exception:
                    pass
        except ImportError:
            self._has_readline = False

    def _save_history(self) -> None:
        """Persist readline history to ~/.siegfried/data/.history with strict 0600 permissions."""
        if self._has_readline:
            try:
                import readline
                self._history_file.parent.mkdir(parents=True, exist_ok=True)
                readline.write_history_file(str(self._history_file))
                if self._history_file.exists():
                    os.chmod(self._history_file, 0o600)
            except Exception:
                pass

    def _check_daemon_connection(self) -> bool:
        """Probe daemon connectivity with non-blocking 0.2s probe."""
        try:
            res = self.client.call(IPCCommand.PING, timeout_seconds=0.2)
            return bool(res and res.status == IPCStatus.OK.value)
        except Exception:
            return False

    def _format_status_payload(self, p: Dict[str, Any]) -> str:
        """Format daemon STATUS response into human-readable overview."""
        state = p.get("state", "DESCONOCIDO")
        sitting_sec = p.get("continuous_sitting_seconds", 0.0)
        sitting_min = round(sitting_sec / 60.0, 1)
        remaining_sec = p.get("remaining_seconds", 0.0)
        remaining_min = round(remaining_sec / 60.0, 1)
        total_sec = p.get("total_duration_seconds", 0.0)
        total_min = round(total_sec / 60.0, 1)
        task_name = p.get("task_name") or "Sin tarea activa"
        timer_active = p.get("timer_active", False)
        audio_playing = p.get("audio_playing", False)

        lines = [
            "Siegfried — Estado del Sistema:",
            f"  • Estado: {state}",
        ]
        if timer_active:
            lines.append(f"  • Bloque activo: '{task_name}' ({remaining_min} min restantes de {total_min} min)")
        else:
            lines.append(f"  • Temporizador: Inactivo ({task_name})")

        posture_warn_in = max(0.0, round(50.0 - sitting_min, 1))
        posture_limit_in = max(0.0, round(60.0 - sitting_min, 1))
        lines.append(
            f"  • Tiempo sentado: {sitting_min} min (Aviso en {posture_warn_in} min, límite en {posture_limit_in} min)"
        )
        audio_str = "REPRODUCIENDO ALARMA" if audio_playing else "Silencio"
        lines.append(f"  • Audio/Alarma: {audio_str}")

        return "\n".join(lines)

    def _check_emergency_silence(self) -> bool:
        """Check if an active alarm is playing and silence it immediately (Enter or Space)."""
        try:
            res = self.client.call(IPCCommand.STATUS, timeout_seconds=0.2)
            if res.status == IPCStatus.OK.value:
                payload = res.payload
                audio_playing = payload.get("audio_playing", False)
                state = payload.get("state", "")
                if audio_playing or state == SystemState.CRITICAL_BREAK_REQUIRED.value:
                    ack_res = self.client.call(IPCCommand.ACK_BREAK, timeout_seconds=1.0)
                    if ack_res.status == IPCStatus.OK.value:
                        print("Siegfried: Alarma silenciada y descanso iniciado.")
                    else:
                        print(f"Siegfried: Alarma silenciada ({ack_res.error_msg or 'OK'}).")
                    return True
        except Exception:
            pass
        return False

    def run(self) -> None:
        """Main REPL loop."""
        self._setup_readline()
        print("Siegfried v1.0 — A su servicio, Señor.")
        print("Escriba 'salir', 'exit' o Ctrl+D para terminar.\n")

        if not self._check_daemon_connection():
            print("[Aviso] No se detectó comunicación con el daemon en segundo plano.")
            print("        Las operaciones de temporizador y estado no estarán disponibles.\n")

        try:
            while True:
                try:
                    line = input("[Siegfried] > ")
                except EOFError:
                    print("\nHasta luego, Señor.")
                    break
                except KeyboardInterrupt:
                    print("\n[Siegfried] Operación cancelada. Use 'salir' o Ctrl+D para terminar.")
                    continue

                trimmed = line.strip()

                # Emergency silence check on empty or whitespace input
                if not trimmed:
                    if self._check_emergency_silence():
                        continue
                    continue

                if trimmed.lower() in ("salir", "exit", "quit"):
                    if self._has_readline:
                        try:
                            import readline
                            readline.add_history(trimmed)
                        except Exception:
                            pass
                    print("Hasta luego, Señor.")
                    break

                # Route input through Fast-Path router
                match = self.router.route(trimmed)
                if match.is_fast_path and match.command:
                    if self._has_readline:
                        try:
                            import readline
                            readline.add_history(trimmed)
                        except Exception:
                            pass
                    try:
                        resp = self.client.call(match.command, match.args)
                        if resp.status == IPCStatus.OK.value:
                            if match.command == IPCCommand.STATUS:
                                print(self._format_status_payload(resp.payload))
                            else:
                                msg = resp.payload.get("message") or resp.payload
                                print(f"Siegfried: {msg}")
                        elif resp.status == IPCStatus.REJECTED.value:
                            print(f"Siegfried [Aviso]: Solicitud rechazada — {resp.error_msg}")
                        else:
                            print(f"Siegfried [Error]: {resp.error_msg}")
                    except IPCCommunicationError as e:
                        print(f"Siegfried: No se pudo conectar con el daemon en segundo plano ({e}).")
                elif match.is_fast_path and match.direct_response is not None:
                    if self._has_readline:
                        try:
                            import readline
                            readline.add_history(trimmed)
                        except Exception:
                            pass
                    if match.direct_response:
                        print(f"Siegfried: {match.direct_response}")
                else:
                    # Cognitive Path (LLM via Daemon IPC QUERY)
                    # Privacy guarantee: Never record cognitive prompts in readline history
                    if self._has_readline:
                        try:
                            import readline
                            if not hasattr(readline, "set_auto_history"):
                                curr_len = readline.get_current_history_length()
                                if curr_len > 0 and readline.get_history_item(curr_len) == trimmed:
                                    readline.remove_history_item(curr_len - 1)
                        except Exception:
                            pass

                    raw_query = match.args.get("raw_text", trimmed)
                    user_msg = InferenceMessage(role="user", content=raw_query)
                    self._conversation_history.append(user_msg)

                    # Prune conversation history to bounded window (turns * 2)
                    max_messages = max(2, self.max_context_turns * 2)
                    if len(self._conversation_history) > max_messages:
                        self._conversation_history = self._conversation_history[-max_messages:]

                    query_payload: Dict[str, Any] = {
                        "prompt": raw_query,
                        "messages": [m.to_dict() for m in self._conversation_history],
                    }
                    if match.args.get("is_historical"):
                        query_payload["is_historical"] = True
                        query_payload["time_window"] = match.args.get("time_window", "today")

                    try:
                        resp = self.client.call(
                            IPCCommand.QUERY,
                            query_payload,
                            timeout_seconds=15.0,
                        )
                        if resp.status == IPCStatus.OK.value:
                            content = resp.payload.get("response", "")
                            route = resp.payload.get("route_used", "")
                            fallback = resp.payload.get("fallback_used", False)
                            tag = f" [{route}]" if route else ""
                            if fallback:
                                tag += " (fallback)"
                            print(f"Siegfried{tag}: {content}")

                            # Record assistant turn into ephemeral session context
                            self._conversation_history.append(
                                InferenceMessage(role="assistant", content=content)
                            )
                        elif resp.status == IPCStatus.REJECTED.value:
                            if resp.payload.get("code") == "INFERENCE_BUSY" or resp.payload.get("error_code") == "INFERENCE_BUSY":
                                print(f"Siegfried [Ocupado]: {resp.error_msg or 'El motor de inferencia está ocupado. Inténtelo más tarde.'}")
                            else:
                                print(f"Siegfried [Aviso]: Solicitud rechazada — {resp.error_msg}")
                        else:
                            print(f"Siegfried [Error]: {resp.error_msg}")
                    except KeyboardInterrupt:
                        print("\nSiegfried: Consulta cancelada por el usuario.")
                    except IPCCommunicationError as e:
                        print(f"Siegfried: No se pudo conectar con el daemon en segundo plano ({e}).")
        finally:
            self._save_history()
