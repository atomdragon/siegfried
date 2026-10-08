"""Atomic JSON read/write operations using independent lockfiles.

CRITICAL RULE:
flock(lock_fd, LOCK_EX) -> write to .tmp -> flush -> fsync -> os.replace() -> unlock(lock_fd)
This avoids POSIX inode replacement breaking active fcntl locks.
"""

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Dict
from siegfried.core.errors import StorageError


def atomic_write_json(
    target_file: Path,
    data: Dict[str, Any],
    lock_file: Path | None = None,
    timeout_seconds: float = 10.0
) -> None:
    """Safely write JSON data using an independent lockfile and atomic file replacement."""
    target_file = Path(target_file)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    
    if lock_file is None:
        lock_file = target_file.with_name(f"{target_file.stem}.lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    tmp_file = target_file.with_name(f"{target_file.name}.tmp.{os.getpid()}_{time.monotonic_ns()}")

    lock_fd = None
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)

        # Acquire exclusive non-blocking lock with deadline backoff
        acquired = False
        deadline = time.monotonic() + timeout_seconds
        delay = 0.001
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(delay, 0.05))
                delay *= 1.5

        if not acquired:
            raise StorageError(f"Could not acquire lock for {target_file} on {lock_file} (timed out after {timeout_seconds}s)")

        # Write to temporary file with explicit 0600 permissions
        serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp_fd = os.open(tmp_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with open(tmp_fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())

        # Atomic replace across same filesystem directory
        os.replace(tmp_file, target_file)

        # Durability: fsync parent directory to persist directory entry changes
        try:
            dir_fd = os.open(target_file.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    except Exception as e:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass
        if isinstance(e, StorageError):
            raise
        raise StorageError(f"Failed atomic write to {target_file}: {e}") from e
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass


def read_json_locked(
    target_file: Path,
    lock_file: Path | None = None,
    default: Dict[str, Any] | None = None,
    timeout_seconds: float = 10.0
) -> Dict[str, Any]:
    """Read JSON safely using a shared lock on the lockfile."""
    target_file = Path(target_file)
    if not target_file.exists():
        if default is not None:
            return dict(default)
        raise StorageError(f"File not found: {target_file}")

    if lock_file is None:
        lock_file = target_file.with_name(f"{target_file.stem}.lock")

    lock_fd = None
    try:
        if lock_file.exists():
            lock_fd = os.open(lock_file, os.O_RDWR, 0o600)
            acquired = False
            deadline = time.monotonic() + timeout_seconds
            delay = 0.001
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(min(delay, 0.05))
                    delay *= 1.5
            if not acquired:
                raise StorageError(f"Could not acquire shared lock on {lock_file} (timed out after {timeout_seconds}s)")

        with open(target_file, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        if isinstance(e, StorageError):
            raise
        raise StorageError(f"Failed to read {target_file}: {e}") from e
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
