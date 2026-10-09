"""Alert Coordinator for Gate F3.2.

Orchestrates deterministic desktop notifications and sensory audio alerts.
Maintains strict separation of concerns:
- State Machine: Transitions and validation decisions.
- Timer: Monotonic expirations.
- Vault: Immutable Event Schema v1 persistence.
- Alert Coordinator: Coordinates sensory alerts, idempotency, and non-blocking delivery.
- Notification Backend: Freedesktop desktop notifications.
- Audio Controller: Surgical process-tracked audio playback and cancellation.
"""

from collections import OrderedDict
import os
from pathlib import Path
import queue
import threading
import time
from typing import Dict, List, Optional, Set

from siegfried.contracts.alerts import (
    Alert,
    AlertType,
    AlertUrgency,
    DeliveryStatus,
    NotificationAttempt,
)
from siegfried.integrations.audio import AudioPlayer
from siegfried.integrations.notifications import NotificationSender, StubNotificationSender


MAX_IDEMPOTENT_EVENT_IDS: int = 1000


class AlertCoordinator:
    """Coordinates sensory alerts (KDE notifications and audio alerts) with idempotency."""

    def __init__(
        self,
        notifier: NotificationSender,
        audio_player: AudioPlayer,
        alert_sound_file: Optional[Path] = None,
        max_queue_size: int = 16,
    ) -> None:
        self.notifier = notifier
        self.audio_player = audio_player
        self.alert_sound_file = Path(alert_sound_file) if alert_sound_file else None
        self.max_queue_size = int(max_queue_size)

        self._queue: queue.Queue[Optional[Alert]] = queue.Queue(maxsize=self.max_queue_size)
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # Idempotency caches: temporal key window and bounded stable domain event_id registry
        self._delivered_keys: Dict[str, float] = {}
        self._delivered_event_ids: OrderedDict[str, float] = OrderedDict()
        self._attempts: List[NotificationAttempt] = []
        self._clean_shutdown: bool = True
        self._force_queue_for_stubs: bool = False

    @property
    def pending_alerts_count(self) -> int:
        """Current number of alerts waiting in delivery queue."""
        return self._queue.qsize()

    @property
    def clean_shutdown(self) -> bool:
        """Whether the last shutdown completed cleanly without hung workers."""
        with self._lock:
            return self._clean_shutdown

    @property
    def attempts(self) -> List[NotificationAttempt]:
        """Delivery attempt audit history."""
        with self._lock:
            return list(self._attempts)

    def is_event_processed(self, event_id: str) -> bool:
        """Check if a domain event ID was already admitted or processed."""
        if not event_id:
            return False
        with self._lock:
            return event_id in self._delivered_event_ids

    def _mark_event_admitted(self, event_id: Optional[str]) -> None:
        """Record admitted event_id in bounded FIFO idempotency cache."""
        if not event_id:
            return
        self._delivered_event_ids[event_id] = time.monotonic()
        if len(self._delivered_event_ids) > MAX_IDEMPOTENT_EVENT_IDS:
            self._delivered_event_ids.popitem(last=False)

    def _unmark_event(self, event_id: Optional[str]) -> None:
        """Evict event_id from idempotency cache (e.g. if queue eviction occurred)."""
        if event_id:
            self._delivered_event_ids.pop(event_id, None)

    def reset_posture_alerts(self) -> None:
        """Reset posture alert deduplication markers upon verified break acknowledgment."""
        with self._lock:
            self._delivered_keys.pop("posture_warning", None)
            self._delivered_keys.pop("posture_limit_reached", None)

    def clear_idempotency_cache(self) -> None:
        """Clear all deduplication and event idempotency caches."""
        with self._lock:
            self._delivered_keys.clear()
            self._delivered_event_ids.clear()

    def start(self) -> None:
        """Start background alert delivery worker thread."""
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="siegfried-alert-worker",
                daemon=True,
            )
            self._worker_thread.start()

    def stop(self, timeout_seconds: float = 1.0) -> bool:
        """Stop alert worker cleanly and terminate active audio playback."""
        # 1. Stop active audio first
        self.stop_audio()

        # 2. Signal stop event
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)  # Sentinel to unblock get()
        except queue.Full:
            try:
                # If queue is full, evict one item to enqueue sentinel
                self._queue.get_nowait()
                self._queue.put_nowait(None)
            except Exception:
                pass

        t = self._worker_thread
        clean = True
        if t is not None and t.is_alive():
            t.join(timeout=max(0.1, timeout_seconds))
            if t.is_alive():
                clean = False
            else:
                self._worker_thread = None

        # 3. Drain and record unprocessed alerts remaining in queue
        with self._lock:
            self._clean_shutdown = clean
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    if item is not None:
                        self._attempts.append(NotificationAttempt(
                            alert_id=item.alert_id,
                            attempted_at=time.time(),
                            accepted=False,
                            error_msg="Shutdown before alert delivery completed",
                            status=DeliveryStatus.FAILED.value,
                        ))
                except queue.Empty:
                    break

        return clean

    def flush(self, timeout_seconds: float = 1.0) -> None:
        """Wait for pending queue items to be processed."""
        deadline = time.monotonic() + timeout_seconds
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.01)

    def _is_duplicate(self, alert_key: str, window_seconds: float = 5.0) -> bool:
        """Check if an identical alert was recently dispatched within window_seconds."""
        now = time.monotonic()
        with self._lock:
            # Prune expired keys
            expired = [k for k, ts in self._delivered_keys.items() if (now - ts) > window_seconds]
            for k in expired:
                del self._delivered_keys[k]

            if alert_key in self._delivered_keys:
                return True
            self._delivered_keys[alert_key] = now
            return False

    def _dispatch_immediate_or_queue(self, alert: Alert) -> bool:
        """Dispatch alert to backend, prioritizing CRITICAL posture barriers and enforcing idempotency."""
        with self._lock:
            # 1. Atomic event ID idempotency verification
            if alert.event_id:
                if alert.event_id in self._delivered_event_ids:
                    self._attempts.append(NotificationAttempt(
                        alert_id=alert.alert_id,
                        attempted_at=time.time(),
                        accepted=False,
                        error_msg=f"Suppressed: duplicate event_id '{alert.event_id}'",
                        status=DeliveryStatus.SUPPRESSED.value,
                    ))
                    return False

            # 2. Test stubs run synchronously unless queue testing is forced
            if isinstance(self.notifier, StubNotificationSender) and not self._force_queue_for_stubs:
                accepted = self._execute_delivery(alert)
                if alert.event_id:
                    self._mark_event_admitted(alert.event_id)
                return accepted

            # 3. Production desktop notifications run via bounded background worker
            if not self._worker_thread or not self._worker_thread.is_alive():
                self.start()

            try:
                self._queue.put_nowait(alert)
                if alert.event_id:
                    self._mark_event_admitted(alert.event_id)
                return True
            except queue.Full:
                with self._queue.mutex:
                    if alert.urgency == AlertUrgency.CRITICAL.value:
                        # Look for non-critical item to evict (LOW priority first, then NORMAL)
                        evict_idx = -1
                        for idx, item in enumerate(self._queue.queue):
                            if item is not None and item.urgency == AlertUrgency.LOW.value:
                                evict_idx = idx
                                break
                        if evict_idx == -1:
                            for idx, item in enumerate(self._queue.queue):
                                if item is not None and item.urgency == AlertUrgency.NORMAL.value:
                                    evict_idx = idx
                                    break

                        if evict_idx != -1:
                            evicted_item = self._queue.queue[evict_idx]
                            del self._queue.queue[evict_idx]
                            self._queue.queue.append(alert)
                            self._queue.not_empty.notify()
                            if evicted_item is not None:
                                self._unmark_event(evicted_item.event_id)
                                self._attempts.append(NotificationAttempt(
                                    alert_id=evicted_item.alert_id,
                                    attempted_at=time.time(),
                                    accepted=False,
                                    error_msg="Evicted from alert queue due to saturation by CRITICAL alert",
                                    status=DeliveryStatus.FAILED.value,
                                ))
                            if alert.event_id:
                                self._mark_event_admitted(alert.event_id)
                            return True
                        else:
                            # Queue is completely full of CRITICAL alerts.
                            # Preserve existing critical alerts in queue; record delivery rejection.
                            # DO NOT mark event_id as admitted so retries remain possible.
                            self._attempts.append(NotificationAttempt(
                                alert_id=alert.alert_id,
                                attempted_at=time.time(),
                                accepted=False,
                                error_msg="Alert queue saturated exclusively with CRITICAL alerts (capacity reached)",
                                status=DeliveryStatus.FAILED.value,
                            ))
                            return False
                    else:
                        # LOW or NORMAL priority dropped under saturation
                        # DO NOT mark event_id as admitted so retries remain possible.
                        self._attempts.append(NotificationAttempt(
                            alert_id=alert.alert_id,
                            attempted_at=time.time(),
                            accepted=False,
                            error_msg="Alert queue saturated (max_queue_size reached)",
                            status=DeliveryStatus.FAILED.value,
                        ))
                        return False

    def _execute_delivery(self, alert: Alert) -> bool:
        """Execute the actual delivery to notification sender and audio player."""
        accepted = False
        error_msg = None
        try:
            accepted = self.notifier.send(
                title=alert.title,
                message=alert.message,
                urgency=alert.urgency
            )
        except Exception as e:
            error_msg = str(e)
            accepted = False

        status_val = DeliveryStatus.DELIVERED.value if accepted else DeliveryStatus.FAILED.value
        with self._lock:
            self._attempts.append(NotificationAttempt(
                alert_id=alert.alert_id,
                attempted_at=time.time(),
                accepted=accepted,
                error_msg=error_msg,
                status=status_val,
            ))

        # Audio playback if requested and file is present
        if alert.sound_path and alert.sound_path.exists():
            try:
                self.audio_player.play_alert(alert.sound_path)
            except Exception:
                pass

        return accepted

    def _worker_loop(self) -> None:
        """Worker loop processing alerts asynchronously."""
        while not self._stop_event.is_set():
            try:
                alert = self._queue.get(timeout=0.1)
                if alert is None:
                    break
                try:
                    self._execute_delivery(alert)
                finally:
                    self._queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                continue


    def _suppress_duplicate_event(self, event_id: str) -> None:
        """Record duplicate event suppression in audit history."""
        with self._lock:
            self._attempts.append(NotificationAttempt(
                alert_id=f"alt-dup-{event_id}",
                attempted_at=time.time(),
                accepted=False,
                error_msg=f"Suppressed: duplicate event_id '{event_id}'",
                status=DeliveryStatus.SUPPRESSED.value,
            ))

    def trigger_pomodoro_completed(self, task_name: str, duration_sec: float, event_id: Optional[str] = None) -> Optional[Alert]:
        """Trigger alert for Pomodoro focus block completion."""
        if event_id and self.is_event_processed(event_id):
            self._suppress_duplicate_event(event_id)
            return None
        task_label = task_name or "Bloque General"
        dedup_key = f"pomodoro_completed:{task_label}"
        if not event_id and self._is_duplicate(dedup_key, window_seconds=3.0):
            return None

        alert = Alert(
            alert_id=f"alt-pomo-{os.urandom(4).hex()}",
            alert_type=AlertType.POMODORO_COMPLETED,
            title="Siegfried — Bloque Concluido",
            message=f"Buen trabajo, Señor. Bloque de '{task_label}' finalizado. Inicie su descanso.",
            urgency=AlertUrgency.NORMAL.value,
            timestamp=time.time(),
            sound_path=self.alert_sound_file,
            event_id=event_id,
        )
        self._dispatch_immediate_or_queue(alert)
        return alert

    def trigger_posture_warning(self, sitting_sec: float, event_id: Optional[str] = None) -> Optional[Alert]:
        """Trigger informative posture warning at 50 continuous sitting minutes."""
        if event_id and self.is_event_processed(event_id):
            self._suppress_duplicate_event(event_id)
            return None
        dedup_key = "posture_warning"
        if not event_id and self._is_duplicate(dedup_key, window_seconds=60.0):
            return None

        mins = int(sitting_sec // 60)
        alert = Alert(
            alert_id=f"alt-posture-warn-{os.urandom(4).hex()}",
            alert_type=AlertType.POSTURE_WARNING,
            title="Siegfried — Aviso Postural",
            message=f"Ha alcanzado {mins} minutos de trabajo continuo. Considere una pausa pronto.",
            urgency=AlertUrgency.NORMAL.value,
            timestamp=time.time(),
            sound_path=None,
            event_id=event_id,
        )
        self._dispatch_immediate_or_queue(alert)
        return alert

    def trigger_posture_limit(self, sitting_sec: float, event_id: Optional[str] = None) -> Optional[Alert]:
        """Trigger sensory alarm for hard 60m posture barrier (critical priority)."""
        if event_id and self.is_event_processed(event_id):
            self._suppress_duplicate_event(event_id)
            return None
        dedup_key = "posture_limit_reached"
        if not event_id and self._is_duplicate(dedup_key, window_seconds=60.0):
            return None

        alert = Alert(
            alert_id=f"alt-posture-crit-{os.urandom(4).hex()}",
            alert_type=AlertType.POSTURE_LIMIT_REACHED,
            title="Siegfried — Límite Postural Alcanzado",
            message="Límite de 60 minutos sentado alcanzado. Prórrogas bloqueadas. Debe realizar una pausa activa.",
            urgency=AlertUrgency.CRITICAL.value,
            timestamp=time.time(),
            sound_path=self.alert_sound_file,
            event_id=event_id,
        )
        self._dispatch_immediate_or_queue(alert)
        return alert

    def trigger_break_started(self, interrupted_audio: bool = False, event_id: Optional[str] = None) -> Optional[Alert]:
        """Trigger informative desktop notice when break commences."""
        if event_id and self.is_event_processed(event_id):
            self._suppress_duplicate_event(event_id)
            return None
        dedup_key = "break_started"
        if not event_id and self._is_duplicate(dedup_key, window_seconds=3.0):
            return None

        alert = Alert(
            alert_id=f"alt-break-{os.urandom(4).hex()}",
            alert_type=AlertType.BREAK_STARTED,
            title="Siegfried — Descanso Iniciado",
            message="Pausa activa en curso. Descanse la vista y estire el cuerpo.",
            urgency=AlertUrgency.LOW.value,
            timestamp=time.time(),
            sound_path=None,
            event_id=event_id,
        )
        self._dispatch_immediate_or_queue(alert)
        return alert

    def trigger_pomodoro_cancelled(self, task_name: str, event_id: Optional[str] = None) -> Optional[Alert]:
        """Trigger notice when a focus block is cancelled and silence any playing audio."""
        self.stop_audio()
        if event_id and self.is_event_processed(event_id):
            self._suppress_duplicate_event(event_id)
            return None
        task_label = task_name or "Bloque General"
        dedup_key = f"pomodoro_cancelled:{task_label}"
        if not event_id and self._is_duplicate(dedup_key, window_seconds=2.0):
            return None


        alert = Alert(
            alert_id=f"alt-pomo-cancel-{os.urandom(4).hex()}",
            alert_type=AlertType.POMODORO_CANCELLED,
            title="Siegfried — Bloque Cancelado",
            message=f"Bloque de '{task_label}' cancelado.",
            urgency=AlertUrgency.LOW.value,
            timestamp=time.time(),
            sound_path=None,
            event_id=event_id,
        )
        self._dispatch_immediate_or_queue(alert)
        return alert


    # ──────────────────────────────────────────────────────────
    # Audio Delegation & Surgical Control
    # ──────────────────────────────────────────────────────────

    def stop_audio(self) -> bool:
        """Surgically terminate sensory alert audio playback."""
        return self.audio_player.stop_alert()

    def is_audio_playing(self) -> bool:
        """Check whether sensory alert audio is actively playing."""
        return self.audio_player.is_playing()
