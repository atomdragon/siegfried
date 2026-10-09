"""Audio alert playback with surgical process custody.

CRITICAL ARCHITECTURAL RULES:
1. NEVER execute global `pkill`, `killall`, `pulseaudio -k` or similar commands.
2. The exact subprocess.Popen instance and PID must be stored and terminated specifically.
3. Only child processes spawned by Siegfried may ever be terminated.
4. Non-blocking asynchronous playback: play_alert returns immediately.
5. Safe fallback across available Linux audio backends (pw-cat, pw-play, paplay, aplay).
"""

from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Optional, Protocol

AUTHORIZED_AUDIO_EXTENSIONS = {".ogg", ".wav", ".oga", ".flac"}


class AudioPlayer(Protocol):
    """Protocol for sensory audio playback and cancellation."""

    def play_alert(self, sound_path: Path) -> bool:
        ...

    def stop_alert(self) -> bool:
        ...

    def is_playing(self) -> bool:
        ...


class PipeWireAudioPlayer:
    """Linux audio player with multiple backend discovery and strict PID custody."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_process: Optional[subprocess.Popen] = None
        self._current_pid: Optional[int] = None
        self._current_sound_path: Optional[Path] = None

        # Discover native Linux audio backends in priority order
        self._pw_cat = shutil.which("pw-cat")
        self._pw_play = shutil.which("pw-play")
        self._paplay = shutil.which("paplay")
        self._aplay = shutil.which("aplay")

    @property
    def current_pid(self) -> Optional[int]:
        """Surgically tracked PID of the active audio playback process."""
        with self._lock:
            if self._current_process and self._current_process.poll() is None:
                return self._current_pid
            return None

    def _resolve_backend_cmd(self, sound_path: Path) -> Optional[list[str]]:
        """Resolve command list for the best available audio backend."""
        path_str = str(sound_path)
        if self._pw_cat:
            return [self._pw_cat, "-p", path_str]
        if self._pw_play:
            return [self._pw_play, path_str]
        if self._paplay:
            return [self._paplay, path_str]
        if self._aplay:
            return [self._aplay, "-q", path_str]
        return None

    def _validate_sound_file(self, sound_path: Path) -> bool:
        """Validate that target sound is an authorized regular audio file."""
        try:
            p = Path(sound_path).resolve()
            if not p.exists() or not p.is_file():
                return False
            if p.suffix.lower() not in AUTHORIZED_AUDIO_EXTENSIONS:
                return False
            return True
        except (OSError, RuntimeError):
            return False

    def play_alert(self, sound_path: Path) -> bool:
        """Play sensory alert sound asynchronously with exact process tracking."""
        sound_path = Path(sound_path)
        if not self._validate_sound_file(sound_path):
            return False

        with self._lock:
            # 1. Prevent duplicate playback if the exact same sound is already actively playing
            if self._current_process is not None and self._current_process.poll() is None:
                if self._current_sound_path == sound_path:
                    return True
                # Different sound: stop previous one first
                self._stop_alert_locked()

            cmd = self._resolve_backend_cmd(sound_path)
            if not cmd:
                return False

            try:
                self._current_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self._current_pid = self._current_process.pid
                self._current_sound_path = sound_path
                return True
            except (subprocess.SubprocessError, OSError):
                self._current_process = None
                self._current_pid = None
                self._current_sound_path = None
                return False

    def stop_alert(self) -> bool:
        """Surgically terminate only our tracked audio player process."""
        with self._lock:
            return self._stop_alert_locked()

    def _stop_alert_locked(self) -> bool:
        """Internal helper executing termination under lock."""
        proc = self._current_process
        if proc is None:
            return False

        if proc.poll() is not None:
            # Process already completed on its own
            self._current_process = None
            self._current_pid = None
            self._current_sound_path = None
            return False

        try:
            # Send SIGTERM first for graceful audio drain
            proc.terminate()
            t_start = time.monotonic()
            while proc.poll() is None and (time.monotonic() - t_start) < 0.5:
                time.sleep(0.02)

            # Escalate to SIGKILL if still hanging
            if proc.poll() is None:
                proc.kill()

            # Always reap child process to prevent zombies
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass

            return True
        except (ProcessLookupError, OSError):
            return False
        finally:
            self._current_process = None
            self._current_pid = None
            self._current_sound_path = None

    def is_playing(self) -> bool:
        """Check if our tracked audio playback process is alive."""
        with self._lock:
            if self._current_process is None:
                return False
            if self._current_process.poll() is not None:
                self._current_process = None
                self._current_pid = None
                self._current_sound_path = None
                return False
            return True


class StubAudioPlayer:
    """Test stub for hermetic testing of sensory audio playback."""

    def __init__(self) -> None:
        self.playing: bool = False
        self.played_files: list[Path] = []
        self._mock_pid: Optional[int] = None

    @property
    def current_pid(self) -> Optional[int]:
        return self._mock_pid if self.playing else None

    def play_alert(self, sound_path: Path) -> bool:
        self.playing = True
        self.played_files.append(Path(sound_path))
        self._mock_pid = 4242
        return True

    def stop_alert(self) -> bool:
        was_playing = self.playing
        self.playing = False
        self._mock_pid = None
        return was_playing

    def is_playing(self) -> bool:
        return self.playing
