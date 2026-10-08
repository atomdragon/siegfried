"""Siegfried Daemon core application.

Manages:
- Unix domain socket server
- Monotonic timers and health state machine
- Event emission to Vault (Event Schema v1)
- Notifications and sensory alerts with exact PID custody
"""

import signal
import sys
import time
from typing import Any, Dict
from siegfried.contracts.states import SystemState
from siegfried.contracts.events import Event, EventType
from siegfried.contracts.ipc import IPCRequest, IPCResponse, IPCCommand, IPCStatus
from siegfried.core.state_machine import HealthStateMachine
from siegfried.core.errors import InvalidStateTransitionError, PostureLimitReachedError, StorageError
from siegfried.storage.paths import SiegfriedPaths, default_paths
from siegfried.storage.vault import Vault
from siegfried.ipc.server import IPCServer
from siegfried.daemon.timers import MonotonicTimer
from siegfried.integrations.notifications import NotificationSender, DesktopNotificationSender
from siegfried.integrations.audio import AudioPlayer, PipeWireAudioPlayer
from siegfried.observability.logging import setup_logger


class SiegfriedDaemon:
    """Core daemon managing state, timers, IPC and hardware alerts."""

    def __init__(
        self,
        paths: SiegfriedPaths | None = None,
        notifier: NotificationSender | None = None,
        audio_player: AudioPlayer | None = None,
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

    def stop(self) -> None:
        """Clean shutdown of daemon."""
        if self._stopped:
            return
        self._stopped = True
        self.logger.info("Deteniendo Siegfried Daemon...")
        self._running = False
        self.audio_player.stop_alert()
        self.ipc_server.stop()
        self.logger.info("Siegfried Daemon detenido correctamente.")

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

        else:
            return IPCResponse.error(req.request_id, f"Comando desconocido: {cmd}")
