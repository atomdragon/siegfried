"""Centralized filesystem path resolution for Siegfried.

RULE: No module should ever manually construct ~/.siegfried or /run/user paths.
All path lookups must flow through this module or SiegfriedPaths instances.
"""

import os
from pathlib import Path


class SiegfriedPaths:
    """Encapsulates runtime directory and file locations."""

    def __init__(self, base_dir: Path | None = None, runtime_dir: Path | None = None) -> None:
        if base_dir is None:
            env_home = os.environ.get("SIEGFRIED_HOME")
            if env_home:
                self.base_dir = Path(env_home).expanduser().resolve()
            else:
                self.base_dir = (Path.home() / ".siegfried").resolve()
        else:
            self.base_dir = base_dir.expanduser().resolve()

        if runtime_dir is None:
            env_run = os.environ.get("XDG_RUNTIME_DIR")
            if env_run:
                self.runtime_dir = Path(env_run).resolve()
            else:
                uid = os.getuid() if hasattr(os, "getuid") else 1000
                self.runtime_dir = Path(f"/run/user/{uid}").resolve()
        else:
            self.runtime_dir = runtime_dir.expanduser().resolve()

    # Subdirectories
    @property
    def config_dir(self) -> Path:
        return self.base_dir / "config"

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def assets_dir(self) -> Path:
        return self.base_dir / "assets"

    @property
    def sounds_dir(self) -> Path:
        return self.assets_dir / "sounds"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    # Config files
    @property
    def secrets_file(self) -> Path:
        return self.config_dir / "secrets.env"

    @property
    def core_profile_file(self) -> Path:
        return self.config_dir / "core_profile.json"

    @property
    def active_agenda_file(self) -> Path:
        return self.config_dir / "active_agenda.json"

    @property
    def active_agenda_lock_file(self) -> Path:
        return self.config_dir / "active_agenda.lock"

    # Data files
    @property
    def vault_file(self) -> Path:
        return self.data_dir / "siegfried_vault.jsonl"

    @property
    def vault_corrupt_log(self) -> Path:
        return self.data_dir / "vault.corrupt.log"

    @property
    def history_file(self) -> Path:
        return self.data_dir / ".history"

    # Ephemeral / IPC files in runtime_dir (/run/user/$UID)
    @property
    def socket_file(self) -> Path:
        return self.runtime_dir / "siegfried.sock"

    @property
    def llama_pid_file(self) -> Path:
        return self.runtime_dir / "siegfried_llama.pid"

    # Asset files
    @property
    def alert_sound_file(self) -> Path:
        return self.sounds_dir / "leaver.ogg"

    def ensure_directories(self) -> None:
        """Create necessary directory structure if missing with 0700 permissions."""
        old_umask = os.umask(0o077)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self.config_dir.mkdir(parents=True, exist_ok=True)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.assets_dir.mkdir(parents=True, exist_ok=True)
            self.sounds_dir.mkdir(parents=True, exist_ok=True)
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            # Attempt runtime dir creation (may already exist via systemd pam)
            try:
                self.runtime_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        finally:
            os.umask(old_umask)


# Global singleton instance for default paths
default_paths = SiegfriedPaths()
