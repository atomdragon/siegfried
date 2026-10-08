"""Audio alert playback with surgical process custody.

CRITICAL RULE:
NEVER execute global `pkill pw-cat` or `pkill paplay`.
The exact subprocess.Popen instance and PID must be stored and terminated specifically.
"""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol


class AudioPlayer(Protocol):
    """Protocol for sensory audio playback and cancellation."""
    def play_alert(self, sound_path: Path) -> bool:
        ...

    def stop_alert(self) -> bool:
        ...

    def is_playing(self) -> bool:
        ...


class PipeWireAudioPlayer:
    """Audio player using pw-cat with fallback to paplay and exact PID tracking."""

    def __init__(self) -> None:
        self._current_process: subprocess.Popen | None = None
        self._pw_cat = shutil.which("pw-cat")
        self._paplay = shutil.which("paplay")

    def play_alert(self, sound_path: Path) -> bool:
        sound_path = Path(sound_path)
        if not sound_path.exists():
            return False

        # Stop existing playback if any
        self.stop_alert()

        cmd = None
        if self._pw_cat:
            cmd = [self._pw_cat, "-p", str(sound_path)]
        elif self._paplay:
            cmd = [self._paplay, str(sound_path)]

        if not cmd:
            return False

        try:
            self._current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
        except (subprocess.SubprocessError, OSError):
            self._current_process = None
            return False

    def stop_alert(self) -> bool:
        """Surgically terminate only our tracked audio player process."""
        proc = self._current_process
        if proc is None:
            return False

        if proc.poll() is not None:
            # Already exited
            self._current_process = None
            return False

        try:
            proc.terminate()
            # Give 500 ms to terminate gracefully
            t_start = time.monotonic()
            while proc.poll() is None and (time.monotonic() - t_start) < 0.5:
                time.sleep(0.02)
            if proc.poll() is None:
                proc.kill()
            return True
        except (ProcessLookupError, OSError):
            return False
        finally:
            self._current_process = None

    def is_playing(self) -> bool:
        return self._current_process is not None and self._current_process.poll() is None


class StubAudioPlayer:
    """Test stub for audio player."""

    def __init__(self) -> None:
        self.playing: bool = False
        self.played_files: list[Path] = []

    def play_alert(self, sound_path: Path) -> bool:
        self.playing = True
        self.played_files.append(Path(sound_path))
        return True

    def stop_alert(self) -> bool:
        was_playing = self.playing
        self.playing = False
        return was_playing

    def is_playing(self) -> bool:
        return self.playing
