"""Siegfried Daemon core application.

Manages:
- Unix domain socket server
- Monotonic timers and health state machine
- Event emission to Vault (Event Schema v1)
- Notifications and sensory alerts with exact PID custody
"""

import signal
import sys
import threading
import time
from typing import Any, Dict, Optional
from siegfried.contracts.states import SystemState
from siegfried.contracts.events import Event, EventType
from siegfried.contracts.ipc import IPCRequest, IPCResponse, IPCCommand, IPCStatus
from siegfried.contracts.inference import InferencePolicy, InferenceMessage, InferenceRequest
from siegfried.core.state_machine import HealthStateMachine
from siegfried.core.errors import (
    InvalidStateTransitionError,
    PostureLimitReachedError,
    StorageError,
    InferenceError,
    InferenceBusyError,
    NoAvailableEngineError,
    InferenceDeadlineExceededError,
    PrivacyViolationError,
)
from siegfried.storage.paths import SiegfriedPaths, default_paths
from siegfried.storage.vault import Vault
from siegfried.ipc.server import IPCServer
from siegfried.daemon.timers import MonotonicTimer
from siegfried.integrations.notifications import NotificationSender, DesktopNotificationSender
from siegfried.integrations.audio import AudioPlayer, PipeWireAudioPlayer
from siegfried.observability.logging import setup_logger
import re


def _sanitize_error_text(text: str) -> str:
    """Sanitize error messages to ensure zero leakage of tokens, Bearer headers, or secrets."""
    cleaned = re.sub(r"sk-[a-zA-Z0-9_\-]+", "[REDACTED_KEY]", str(text))
    cleaned = re.sub(r"Bearer\s+[^\s,]+", "Bearer [REDACTED_TOKEN]", cleaned)
    return cleaned


class SiegfriedDaemon:
    """Core daemon managing state, timers, IPC and hardware alerts."""


    def __init__(
        self,
        paths: SiegfriedPaths | None = None,
        notifier: NotificationSender | None = None,
        audio_player: AudioPlayer | None = None,
        orchestrator: Any | None = None,
    ) -> None:
        self.paths = paths or default_paths
        self.paths.ensure_directories()
        self.logger = setup_logger("daemon", log_dir=self.paths.logs_dir)

        self.vault = Vault(self.paths.vault_file, self.paths.vault_corrupt_log)
        self.state_machine = HealthStateMachine()
        self.notifier = notifier or DesktopNotificationSender()
        self.audio_player = audio_player or PipeWireAudioPlayer()

        self.focus_timer = MonotonicTimer(on_expire=self._on_focus_expired)
        self.ipc_server = IPCServer(self.paths.socket_file, handler=self.handle_ipc_request)
        self._running = False
        self._stopped = False
        self._active_task_name = ""
        self._orchestrator = orchestrator
        self._orchestrator_lock = threading.Lock()


    def _validate_startup_runtime(self) -> None:
        """Validate that all existing runtime resources are secure, non-corrupted and safe to use."""
        from siegfried.storage.validation import (
            _check_permissions,
            _check_symlink_safety,
            _verify_directory,
            _verify_regular_file,
            _read_and_validate_json_file,
            _verify_secrets_env,
            _verify_vault,
        )
        from siegfried.contracts.config import validate_core_profile, validate_active_agenda

        base_dir = self.paths.base_dir
        if base_dir.exists():
            _check_symlink_safety(base_dir, base_dir)
            _verify_directory(base_dir)
            _check_permissions(base_dir, is_dir=True)

        for d in [self.paths.config_dir, self.paths.data_dir, self.paths.assets_dir, self.paths.sounds_dir, self.paths.logs_dir]:
            if d.exists():
                _check_symlink_safety(d, base_dir)
                _verify_directory(d)
                _check_permissions(d, is_dir=True)

        if self.paths.secrets_file.exists():
            _check_symlink_safety(self.paths.secrets_file, base_dir)
            _verify_regular_file(self.paths.secrets_file)
            _check_permissions(self.paths.secrets_file, is_dir=False)
            _verify_secrets_env(self.paths.secrets_file)

        if self.paths.core_profile_file.exists():
            _check_symlink_safety(self.paths.core_profile_file, base_dir)
            _verify_regular_file(self.paths.core_profile_file)
            _check_permissions(self.paths.core_profile_file, is_dir=False)
            _read_and_validate_json_file(self.paths.core_profile_file, validate_core_profile)

        if self.paths.active_agenda_file.exists():
            _check_symlink_safety(self.paths.active_agenda_file, base_dir)
            _verify_regular_file(self.paths.active_agenda_file)
            _check_permissions(self.paths.active_agenda_file, is_dir=False)
            _read_and_validate_json_file(self.paths.active_agenda_file, validate_active_agenda)

        if self.paths.vault_file.exists():
            _check_symlink_safety(self.paths.vault_file, base_dir)
            _verify_regular_file(self.paths.vault_file)
            _check_permissions(self.paths.vault_file, is_dir=False)
            _verify_vault(self.paths.vault_file, deep_audit=False)

    def start(self) -> None:
        """Start daemon loop and register signal handlers."""
        self.logger.info("Iniciando Siegfried Daemon...")
        try:
            self._validate_startup_runtime()
        except StorageError as e:
            self.logger.error(f"Fallo de seguridad o validación en runtime: {e}")
            raise
        self.ipc_server.start()
        self._running = True

        # Handle graceful termination
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self.logger.info(f"Siegfried Daemon activo en socket {self.paths.socket_file}")

    def run_tick(self, timeout_seconds: float = 0.05) -> None:
        """Execute a single reactor iteration (process IPC and check timers)."""
        # 1. Process pending IPC connections
        self.ipc_server.poll(timeout_seconds=timeout_seconds)

        # 2. Check timer expiration
        self.focus_timer.check_expiration()

    def run_forever(self) -> None:
        """Main daemon event loop."""
        self.start()
        try:
            while self._running:
                self.run_tick(timeout_seconds=0.1)
        finally:
            self.stop()

    def stop(self, timeout_seconds: float = 2.0) -> bool:
        """Clean shutdown of daemon."""
        if self._stopped:
            return True
        self._stopped = True
        self.logger.info("Deteniendo Siegfried Daemon...")
        self._running = False
        self.audio_player.stop_alert()
        clean = self.ipc_server.stop(timeout_seconds=timeout_seconds)
        if clean:
            self.logger.info("Siegfried Daemon detenido correctamente.")
        else:
            self.logger.warning(
                f"Siegfried Daemon detenido con {len(self.ipc_server.residual_workers)} trabajadores residuales."
            )
        return clean

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self._running = False

    def _on_focus_expired(self) -> None:
        """Callback invoked when focus timer reaches deadline."""
        self.logger.info(f"Bloque de enfoque completado para: '{self._active_task_name}'")
        
        # 1. Record event in Vault
        event_data = {
            "duration_sec": self.focus_timer.snapshot().total_duration_seconds,
            "task": self._active_task_name,
        }
        event = Event.create(EventType.POMODORO_COMPLETED, event_data)
        self.vault.append(event)

        # 2. Transition state machine
        try:
            self.state_machine.transition_to(SystemState.BREAK_RUNNING, reason="Timer focus expired")
        except InvalidStateTransitionError as e:
            self.logger.error(f"Error en transición de estado tras timer: {e}")

        # 3. Fire notification & audio alert
        self.notifier.send(
            title="Siegfried — Bloque Concluido",
            message=f"Buen trabajo, Señor. Bloque de '{self._active_task_name}' finalizado. Inicie su descanso.",
            urgency="normal"
        )
        if self.paths.alert_sound_file.exists():
            self.audio_player.play_alert(self.paths.alert_sound_file)

    def handle_ipc_request(self, req: IPCRequest) -> IPCResponse:
        """Dispatch IPC requests from CLI."""
        cmd = req.cmd

        if cmd == IPCCommand.PING.value:
            return IPCResponse.ok(req.request_id, {"pong": True, "state": self.state_machine.state.value})

        elif cmd == IPCCommand.STATUS.value:
            snapshot = self.focus_timer.snapshot()
            return IPCResponse.ok(req.request_id, {
                "state": self.state_machine.state.value,
                "continuous_sitting_seconds": self.state_machine.continuous_sitting_seconds,
                "timer_active": snapshot.is_active,
                "remaining_seconds": snapshot.remaining_seconds,
                "total_duration_seconds": snapshot.total_duration_seconds,
                "task_name": snapshot.task_name,
                "audio_playing": self.audio_player.is_playing(),
            })

        elif cmd == IPCCommand.START_FOCUS.value:
            duration_min = req.args.get("duration_min", 25)
            task_name = req.args.get("task", "Bloque General")
            duration_sec = float(duration_min) * 60.0

            try:
                self.state_machine.transition_to(SystemState.POMODORO_RUNNING, reason=f"START_FOCUS {task_name}")
            except (InvalidStateTransitionError, PostureLimitReachedError) as e:
                return IPCResponse.rejected(req.request_id, str(e))

            self._active_task_name = str(task_name)
            self.focus_timer.start(duration_seconds=duration_sec, task_name=self._active_task_name)
            
            # Emit event to vault
            event = Event.create(EventType.POMODORO_STARTED, {
                "duration_min": duration_min,
                "task": self._active_task_name
            })
            self.vault.append(event)
            self.logger.info(f"Iniciado bloque de enfoque ({duration_min} min): {self._active_task_name}")

            return IPCResponse.ok(req.request_id, {
                "message": f"Bloque de enfoque iniciado ({duration_min} min).",
                "task": self._active_task_name,
                "state": self.state_machine.state.value
            })

        elif cmd == IPCCommand.CANCEL_FOCUS.value:
            if self.focus_timer.is_active():
                self.focus_timer.cancel()
                try:
                    self.state_machine.transition_to(SystemState.IDLE, reason="CANCEL_FOCUS")
                except InvalidStateTransitionError:
                    pass
                event = Event.create(EventType.POMODORO_CANCELLED, {"task": self._active_task_name})
                self.vault.append(event)
                return IPCResponse.ok(req.request_id, {"message": "Bloque cancelado."})
            return IPCResponse.rejected(req.request_id, "No hay ningún bloque de enfoque activo.")

        elif cmd == IPCCommand.ACK_BREAK.value:
            # Silence audio alert and transition
            was_playing = self.audio_player.stop_alert()
            if self.state_machine.state == SystemState.CRITICAL_BREAK_REQUIRED:
                try:
                    self.state_machine.transition_to(SystemState.BREAK_RUNNING, reason="ACK_BREAK")
                except InvalidStateTransitionError:
                    pass
            event = Event.create(EventType.BREAK_STARTED, {"interrupted_audio": was_playing})
            self.vault.append(event)
            return IPCResponse.ok(req.request_id, {"message": "Alarma silenciada y descanso iniciado.", "audio_stopped": was_playing})

        elif cmd == IPCCommand.SHUTDOWN.value:
            self._running = False
            return IPCResponse.ok(req.request_id, {"message": "Daemon apagándose..."})

        elif cmd == IPCCommand.QUERY.value:
            prompt = req.args.get("prompt") or req.args.get("raw_text")
            if not prompt or not isinstance(prompt, str) or not prompt.strip():
                return IPCResponse.rejected(req.request_id, "Prompt vacío o inválido.")
            if len(prompt) > 32768:
                return IPCResponse.rejected(req.request_id, "Prompt excede tamaño máximo permitido (32 KB).")

            policy_arg = req.args.get("policy")
            policy = None
            if policy_arg:
                try:
                    policy = InferencePolicy(policy_arg)
                except ValueError:
                    return IPCResponse.error(req.request_id, f"Política de inferencia inválida: {policy_arg}")

            timeout_arg = req.args.get("timeout_seconds")
            timeout_seconds = float(timeout_arg) if timeout_arg else 10.0

            messages_arg = req.args.get("messages")
            if messages_arg and isinstance(messages_arg, list):
                try:
                    messages = [InferenceMessage.from_dict(m) for m in messages_arg]
                except Exception as e:
                    return IPCResponse.error(req.request_id, f"Mensajes inválidos: {e}")
            else:
                messages = [InferenceMessage(role="user", content=prompt.strip())]

            inf_req = InferenceRequest(
                messages=messages,
                timeout_seconds=timeout_seconds,
            )

            try:
                orchestrator = self._get_orchestrator()
                result = orchestrator.orchestrate(
                    request=inf_req,
                    policy=policy,
                    request_id=req.request_id,
                )
                route_used_str = (
                    result.route_used.value
                    if hasattr(result.route_used, "value")
                    else str(result.route_used)
                )
                return IPCResponse.ok(req.request_id, {
                    "response": result.response.content,
                    "model": result.response.model,
                    "route_used": route_used_str,
                    "fallback_used": result.fallback_used,
                    "fallback_reason": result.fallback_reason,
                    "elapsed_ms": result.elapsed_ms,
                    "request_id": result.request_id,
                })
            except InferenceBusyError as e:
                msg = _sanitize_error_text(str(e) or "El motor de inferencia está ocupado. Inténtalo nuevamente.")
                return IPCResponse.busy(req.request_id, reason=msg)
            except NoAvailableEngineError as e:
                msg = _sanitize_error_text(f"Motor de inferencia no disponible ({type(e).__name__}): {e}")
                return IPCResponse.error(req.request_id, msg, {"error_type": "NoAvailableEngineError"})
            except InferenceDeadlineExceededError as e:
                msg = _sanitize_error_text(f"Tiempo límite de inferencia agotado ({type(e).__name__}): {e}")
                return IPCResponse.error(req.request_id, msg, {"error_type": "InferenceDeadlineExceededError"})
            except PrivacyViolationError as e:
                msg = _sanitize_error_text(f"Violación de política de privacidad ({type(e).__name__}): {e}")
                return IPCResponse.error(req.request_id, msg, {"error_type": "PrivacyViolationError"})
            except InferenceError as e:
                msg = _sanitize_error_text(f"Fallo en motor de inferencia ({type(e).__name__}): {e}")
                return IPCResponse.error(req.request_id, msg, {"error_type": type(e).__name__})
            except Exception as e:
                self.logger.error(f"Error inesperado durante inferencia: {e}")
                msg = _sanitize_error_text(f"Error inesperado en inferencia ({type(e).__name__}): {e}")
                return IPCResponse.error(req.request_id, msg, {"error_type": "UnexpectedError"})


        else:
            return IPCResponse.error(req.request_id, f"Comando desconocido: {cmd}")

    def _get_orchestrator(self) -> Any:
        """Retrieve or lazily initialize InferenceOrchestrator."""
        with self._orchestrator_lock:
            if self._orchestrator is None:
                self._orchestrator = self._init_orchestrator()
            return self._orchestrator

    def _init_orchestrator(self) -> Any:
        """Lazily initialize InferenceOrchestrator with safe defaults and zero startup blocking."""
        from siegfried.storage.secrets import get_secret
        from siegfried.inference.cloud import CloudInferenceClient
        from siegfried.inference.local import LocalInferenceClient
        from siegfried.inference.llama_manager import LlamaLifecycleManager
        from siegfried.inference.orchestrator import InferenceOrchestrator

        # 1. Cloud Client (lazy check of secrets.env)
        cloud_client = None
        try:
            api_key = get_secret("DEEPSEEK_API_KEY", secrets_file=self.paths.secrets_file)
            if api_key:
                cloud_client = CloudInferenceClient(api_key=api_key, paths=self.paths)
        except Exception as e:
            self.logger.warning(f"No se pudo inicializar cliente Cloud: {e}")

        # 2. Local Manager & Client (lazy check of local model/binary)
        local_client = None
        local_mgr = None
        try:
            local_mgr = LlamaLifecycleManager(paths=self.paths)
            local_client = LocalInferenceClient(manager=local_mgr)
        except Exception as e:
            self.logger.warning(f"No se pudo inicializar gestor Local: {e}")

        # Privacy precedence: default to LOCAL_PREFERRED if cloud is configured, otherwise LOCAL_ONLY
        default_policy = InferencePolicy.LOCAL_PREFERRED if cloud_client else InferencePolicy.LOCAL_ONLY

        return InferenceOrchestrator(
            cloud_client=cloud_client,
            local_client=local_client,
            local_manager=local_mgr,
            default_policy=default_policy,
        )
