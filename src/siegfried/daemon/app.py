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
from siegfried.contracts.states import SystemState, MAX_CONTINUOUS_SITTING_SECONDS
from siegfried.contracts.events import Event, EventType
from siegfried.contracts.ipc import IPCRequest, IPCResponse, IPCCommand, IPCStatus
from siegfried.contracts.inference import InferencePolicy, InferenceMessage, InferenceRequest
from siegfried.core.state_machine import HealthStateMachine
from siegfried.core.errors import (
    InvalidStateTransitionError,
    PostureLimitReachedError,
    StorageError,
    RuntimeNotInitializedError,
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
from siegfried.daemon.alerts import AlertCoordinator
from siegfried.daemon.focus import FocusTracker, FocusDBusAdapter
from siegfried.daemon.session import SessionController
from siegfried.integrations.session import SessionDBusAdapter
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
        # Do not silently create missing base directories on uninitialized runtimes
        if self.paths.base_dir.exists():
            try:
                self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
                self.logger = setup_logger("daemon", log_dir=self.paths.logs_dir)
            except OSError:
                self.logger = setup_logger("daemon", log_dir=None)
        else:
            self.logger = setup_logger("daemon", log_dir=None)

        self._vault: Vault | None = None
        if self.paths.base_dir.exists():
            self._vault = Vault(self.paths.vault_file, self.paths.vault_corrupt_log)
        self.state_machine = HealthStateMachine()
        self.notifier = notifier or DesktopNotificationSender()
        self.audio_player = audio_player or PipeWireAudioPlayer()
        self.alert_coordinator = AlertCoordinator(
            notifier=self.notifier,
            audio_player=self.audio_player,
            alert_sound_file=self.paths.alert_sound_file,
        )

        self.focus_timer = MonotonicTimer(on_expire=self._on_focus_expired)
        self.ipc_server = IPCServer(self.paths.socket_file, handler=self.handle_ipc_request)
        self.focus_tracker = FocusTracker(vault=self._vault)
        self.focus_dbus_adapter = FocusDBusAdapter(self.focus_tracker)
        self.session_controller = SessionController(self.focus_tracker)
        self.session_dbus_adapter = SessionDBusAdapter(self.session_controller)
        self._running = False
        self._stopped = False
        self._shutdown_clean = False
        self._active_task_name = ""
        self._orchestrator = orchestrator
        self._orchestrator_lock = threading.Lock()
        self._last_tick_time: float = time.monotonic()
        self._posture_warned_50m: bool = False
        self._posture_barrier_triggered: bool = False
        self._event_sequence: int = 0

    def _create_event_id(self, event: Event) -> str:
        """Create a collision-resistant deterministic domain event identifier.

        Combines event type, microsecond timestamp, and monotonic daemon sequence counter.
        """
        self._event_sequence += 1
        return f"{event.type}:{event.ts:.6f}:{self._event_sequence}"

    @property
    def vault(self) -> Vault:
        if self._vault is None:
            self._vault = Vault(self.paths.vault_file, self.paths.vault_corrupt_log)
        return self._vault

    @vault.setter
    def vault(self, value: Vault) -> None:
        self._vault = value
        if hasattr(self, "focus_tracker") and self.focus_tracker is not None:
            self.focus_tracker.vault = value


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
        if not base_dir.exists():
            raise RuntimeNotInitializedError(
                f"Runtime no inicializado: el directorio base '{base_dir}' no existe. Ejecute 'siegfried init'."
            )

        _check_symlink_safety(base_dir, base_dir)
        _verify_directory(base_dir)
        _check_permissions(base_dir, is_dir=True)

        mandatory_dirs = [
            self.paths.config_dir,
            self.paths.data_dir,
            self.paths.assets_dir,
            self.paths.sounds_dir,
            self.paths.logs_dir,
        ]
        for d in mandatory_dirs:
            if not d.exists():
                raise RuntimeNotInitializedError(
                    f"Runtime no inicializado: falta el directorio '{d}'. Ejecute 'siegfried init'."
                )
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

    def _recover_deterministic_state(self) -> None:
        """Recover deterministic state from persistent configuration/agenda without modifying files."""
        from siegfried.storage.atomic_json import read_json_locked
        if self.paths.active_agenda_file.exists():
            try:
                agenda = read_json_locked(self.paths.active_agenda_file, timeout_seconds=1.0)
                crit = agenda.get("critical_task")
                if isinstance(crit, dict) and "title" in crit and isinstance(crit["title"], str):
                    self._active_task_name = crit["title"]
            except Exception as e:
                self.logger.warning("No se pudo cargar tarea activa de agenda.")

    def start(self) -> None:
        """Start daemon loop, alert coordinator and register signal handlers."""
        self.logger.info("Iniciando Siegfried Daemon...")
        try:
            self._validate_startup_runtime()
            self._recover_deterministic_state()
        except StorageError as e:
            self.logger.error("Fallo de seguridad o validación en runtime.")
            raise
        if self.focus_tracker.vault is None:
            self.focus_tracker.vault = self.vault
        self.alert_coordinator.start()
        session_started = self.session_dbus_adapter.start()
        self.focus_dbus_adapter.start(mainloop_owner=self.session_dbus_adapter if session_started else None)
        self._last_tick_time = time.monotonic()
        self.ipc_server.start()
        self._running = True

        # Handle graceful termination safely from main thread
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGINT, self._handle_signal)
                signal.signal(signal.SIGTERM, self._handle_signal)
            except (ValueError, AttributeError):
                pass

        self.logger.info(f"Siegfried Daemon activo en socket {self.paths.socket_file}")

    def run_tick(self, timeout_seconds: float = 0.05) -> None:
        """Execute a single reactor iteration (process IPC, check timers, and track posture)."""
        # 1. Process pending IPC connections
        self.ipc_server.poll(timeout_seconds=timeout_seconds)

        # 2. Check timer expiration
        self.focus_timer.check_expiration()

        # 3. Track continuous sitting time during active focus/postpone
        now = time.monotonic()
        elapsed = now - self._last_tick_time
        self._last_tick_time = now

        if self.state_machine.state in (SystemState.POMODORO_RUNNING, SystemState.POSTPONE_RUNNING):
            self.state_machine.add_sitting_time(elapsed)
            self._check_posture_milestones()

    def _check_posture_milestones(self) -> None:
        """Evaluate continuous sitting milestones (50 min warning and 60 min hard limit)."""
        sitting = self.state_machine.continuous_sitting_seconds

        # 50 minutes informative warning
        if sitting >= 3000.0 and not self._posture_warned_50m:
            self._posture_warned_50m = True
            event = Event.create(EventType.POSTURE_WARNING, {"sitting_sec": round(sitting, 1)})
            self.vault.append(event)
            event_id = self._create_event_id(event)
            self.alert_coordinator.trigger_posture_warning(sitting, event_id=event_id)

        # 60 minutes hard barrier
        if sitting >= MAX_CONTINUOUS_SITTING_SECONDS and not self._posture_barrier_triggered:
            self._posture_barrier_triggered = True
            if self.focus_timer.is_active():
                self.focus_timer.cancel()
            event = Event.create(EventType.POSTURE_LIMIT_REACHED, {"sitting_sec": round(sitting, 1)})
            self.vault.append(event)
            event_id = self._create_event_id(event)
            self.alert_coordinator.trigger_posture_limit(sitting, event_id=event_id)

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
            return self._shutdown_clean
        self._stopped = True
        self.logger.info("Deteniendo Siegfried Daemon...")
        self._running = False
        focus_clean = False
        session_clean = True
        session_enabled = self.session_controller.enabled
        if session_enabled:
            # The session worker owns terminal interval closure. Do not wait
            # on its tracker/disk lock from the daemon's shutdown thread.
            try:
                session_clean = self.session_dbus_adapter.stop(timeout=min(1.0, timeout_seconds / 2))
            except Exception:
                session_clean = False
                self.logger.warning("Error deteniendo adaptador de sesión")
        try:
            focus_clean = self.focus_dbus_adapter.stop(discard=session_enabled and not session_clean,
                                                        close_tracker=not session_enabled)
        except Exception:
            self.logger.warning("Error deteniendo adaptador D-Bus de foco")
        if not session_enabled:
            try:
                session_clean = self.session_dbus_adapter.stop(timeout=min(1.0, timeout_seconds / 2))
            except Exception:
                session_clean = False
                self.logger.warning("Error deteniendo adaptador de sesión")
        coord_clean = self.alert_coordinator.stop(timeout_seconds=min(1.0, timeout_seconds / 2))
        ipc_clean = self.ipc_server.stop(timeout_seconds=timeout_seconds)
        clean = bool(coord_clean and ipc_clean and focus_clean and session_clean)
        self._shutdown_clean = clean
        if clean:
            self.logger.info("Siegfried Daemon detenido correctamente.")
        else:
            self.logger.warning(
                f"Siegfried Daemon detenido con trabajadores residuales "
                f"(ipc={ipc_clean}, alert={coord_clean}, focus={focus_clean}, session={session_clean})."
            )
        return clean

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self._running = False

    def _on_focus_expired(self) -> None:
        """Callback invoked when focus timer reaches deadline."""
        task = self.focus_timer.snapshot().task_name or self._active_task_name
        self.logger.info("Bloque de enfoque completado.")
        
        # 1. Record event in Vault
        event_data = {
            "duration_sec": self.focus_timer.snapshot().total_duration_seconds,
            "task": task,
        }
        event = Event.create(EventType.POMODORO_COMPLETED, event_data)
        self.vault.append(event)
        event_id = self._create_event_id(event)

        # 2. Transition state machine
        if self.state_machine.state in (SystemState.POMODORO_RUNNING, SystemState.POSTPONE_RUNNING):
            try:
                self.state_machine.transition_to(SystemState.BREAK_RUNNING, reason="Timer focus expired")
            except InvalidStateTransitionError as e:
                self.logger.error("Error en transición de estado tras timer.")

        # 3. Fire notification & audio alert through AlertCoordinator
        self.alert_coordinator.trigger_pomodoro_completed(
            task_name=task,
            duration_sec=event_data["duration_sec"],
            event_id=event_id,
        )

    def handle_ipc_request(self, req: IPCRequest) -> IPCResponse:
        """Dispatch IPC requests from CLI."""
        cmd = req.cmd

        if cmd == IPCCommand.PING.value:
            return IPCResponse.ok(req.request_id, {"pong": True, "state": self.state_machine.state.value})

        elif cmd == IPCCommand.STATUS.value:
            snapshot = self.focus_timer.snapshot()
            focus_snap = self.focus_tracker.snapshot()
            return IPCResponse.ok(req.request_id, {
                "state": self.state_machine.state.value,
                "continuous_sitting_seconds": round(self.state_machine.continuous_sitting_seconds, 1),
                "timer_active": snapshot.is_active,
                "remaining_seconds": snapshot.remaining_seconds,
                "total_duration_seconds": snapshot.total_duration_seconds,
                "task_name": snapshot.task_name,
                "audio_playing": self.alert_coordinator.is_audio_playing(),
                "focus_active_app": focus_snap.get("active_app"),
                "focus_active_category": focus_snap.get("active_category"),
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
            self._last_tick_time = time.monotonic()
            self.focus_timer.start(duration_seconds=duration_sec, task_name=self._active_task_name)
            
            # Emit event to vault
            event = Event.create(EventType.POMODORO_STARTED, {
                "duration_min": duration_min,
                "task": self._active_task_name
            })
            self.vault.append(event)
            self.logger.info(f"Iniciado bloque de enfoque ({duration_min} min).")

            return IPCResponse.ok(req.request_id, {
                "message": f"Bloque de enfoque iniciado ({duration_min} min).",
                "task": self._active_task_name,
                "state": self.state_machine.state.value
            })

        elif cmd == IPCCommand.CANCEL_FOCUS.value:
            if self.focus_timer.is_active():
                self.focus_timer.cancel()
                self.alert_coordinator.stop_audio()
                try:
                    self.state_machine.transition_to(SystemState.IDLE, reason="CANCEL_FOCUS")
                except InvalidStateTransitionError:
                    pass
                event = Event.create(EventType.POMODORO_CANCELLED, {"task": self._active_task_name})
                self.vault.append(event)
                event_id = self._create_event_id(event)
                self.alert_coordinator.trigger_pomodoro_cancelled(self._active_task_name, event_id=event_id)
                return IPCResponse.ok(req.request_id, {"message": "Bloque cancelado."})
            return IPCResponse.rejected(req.request_id, "No hay ningún bloque de enfoque activo.")

        elif cmd == IPCCommand.POSTPONE.value:
            minutes = req.args.get("minutes", 10)
            try:
                minutes = int(minutes)
            except (ValueError, TypeError):
                minutes = 10
            requested_sec = float(minutes) * 60.0

            if not self.state_machine.can_postpone(requested_sec):
                event = Event.create(EventType.POSTPONE_REJECTED, {
                    "requested_min": minutes,
                    "sitting_sec": round(self.state_machine.continuous_sitting_seconds, 1),
                    "state": self.state_machine.state.value
                })
                self.vault.append(event)
                return IPCResponse.rejected(
                    req.request_id,
                    "Prórroga denegada: límite postural alcanzado (máx 60 min continuo) o estado inválido."
                )

            try:
                self.state_machine.transition_to(SystemState.POSTPONE_RUNNING, reason=f"POSTPONE {minutes} min")
            except (InvalidStateTransitionError, PostureLimitReachedError) as e:
                return IPCResponse.rejected(req.request_id, str(e))

            self.focus_timer.start(duration_seconds=requested_sec, task_name=self._active_task_name)
            self.alert_coordinator.stop_audio()
            event = Event.create(EventType.POSTPONE_GRANTED, {
                "duration_min": minutes,
                "task": self._active_task_name
            })
            self.vault.append(event)
            return IPCResponse.ok(req.request_id, {
                "message": f"Prórroga de {minutes} min concedida.",
                "state": self.state_machine.state.value
            })

        elif cmd == IPCCommand.ACK_BREAK.value:
            # Silence audio alert and transition
            was_playing = self.alert_coordinator.stop_audio()
            if self.state_machine.state == SystemState.CRITICAL_BREAK_REQUIRED:
                try:
                    self.state_machine.transition_to(SystemState.BREAK_RUNNING, reason="ACK_BREAK")
                except InvalidStateTransitionError:
                    pass
            self.state_machine.reset_sitting_time()
            self._posture_warned_50m = False
            self._posture_barrier_triggered = False
            self.alert_coordinator.reset_posture_alerts()
            event = Event.create(EventType.BREAK_STARTED, {"interrupted_audio": was_playing})
            self.vault.append(event)
            event_id = self._create_event_id(event)
            self.alert_coordinator.trigger_break_started(interrupted_audio=was_playing, event_id=event_id)
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

            # Historical detection (C1 / C2)
            is_historical = bool(req.args.get("is_historical", False))
            time_window = req.args.get("time_window", "today")
            if not is_historical:
                from siegfried.cli.router import CommandRouter
                r_match = CommandRouter().route(prompt)
                if r_match.args.get("is_historical"):
                    is_historical = True
                    time_window = r_match.args.get("time_window", "today")

            # Deny-by-default Cloud Privacy Enforcement (C3)
            if is_historical:
                if policy == InferencePolicy.CLOUD_ONLY or policy_arg == "CLOUD_ONLY":
                    return IPCResponse.rejected(
                        req.request_id,
                        "Transmisión de métricas históricas a la nube bloqueada por política de privacidad (deny-by-default). Utilice inferencia LOCAL.",
                    )
                # Force LOCAL_ONLY for any historical telemetry query
                policy = InferencePolicy.LOCAL_ONLY

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

            # Deterministic Context Aggregation and Injection (C2 / C4)
            if is_historical:
                from siegfried.storage.aggregator import HistoricalAggregator, AggregatedMetrics
                try:
                    aggregator = HistoricalAggregator(self.paths.vault_file)
                    if time_window == "week":
                        metrics = aggregator.aggregate_week(allow_early_exit=False)
                    else:
                        metrics = aggregator.aggregate_today(allow_early_exit=False)
                except Exception as e:
                    self.logger.warning("Error al agregar telemetría histórica.")
                    metrics = AggregatedMetrics()

                prompt_block = HistoricalAggregator.format_prompt_block(metrics)
                system_msg = InferenceMessage(role="system", content=prompt_block)
                # Insert immediately before the last user query
                insert_idx = max(0, len(messages) - 1)
                messages.insert(insert_idx, system_msg)

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
                self.logger.error("Error inesperado durante inferencia.")
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
            self.logger.warning("No se pudo inicializar cliente Cloud.")

        # 2. Local Manager & Client (lazy check of local model/binary)
        local_client = None
        local_mgr = None
        try:
            local_mgr = LlamaLifecycleManager(paths=self.paths)
            local_client = LocalInferenceClient(manager=local_mgr)
        except Exception as e:
            self.logger.warning("No se pudo inicializar gestor Local.")

        # Privacy precedence: default to LOCAL_PREFERRED if cloud is configured, otherwise LOCAL_ONLY
        default_policy = InferencePolicy.LOCAL_PREFERRED if cloud_client else InferencePolicy.LOCAL_ONLY

        return InferenceOrchestrator(
            cloud_client=cloud_client,
            local_client=local_client,
            local_manager=local_mgr,
            default_policy=default_policy,
        )
