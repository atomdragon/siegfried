"""Runtime directory scaffolding and secure initialization for Siegfried.

Ensures ~/.siegfried/ structure is created idempotently and securely:
- Strict 0700 directory permissions.
- Strict 0600 file permissions.
- Valid initial configurations conforming to Config Schema v1.
- Empty JSONL vault (Event Schema v1 pure, zero non-JSON headers).
- Empty secrets.env with security comments (zero actual credentials).
- Atomic operations with fcntl locking to prevent race conditions.
- Symlink traversal protection.
"""

from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Callable, Dict, List

from siegfried.contracts.config import (
    get_default_active_agenda,
    get_default_core_profile,
    validate_active_agenda,
    validate_core_profile,
)
from siegfried.contracts.events import Event
from siegfried.core.errors import StorageError
from siegfried.storage.atomic_json import atomic_write_json, read_json_locked
from siegfried.storage.paths import SiegfriedPaths, default_paths


SECRETS_ENV_TEMPLATE = """# Siegfried Secrets Environment
# Guarde aqui sus credenciales y variables de entorno protegidas.
# DEEPSEEK_API_KEY=
"""


@dataclass(frozen=True)
class RuntimeInitResult:
    """Represents the outcome of a runtime initialization or verification."""
    base_dir: Path
    created: bool
    status: str = "READY"
    created_items: List[str] = field(default_factory=list)
    verified_items: List[str] = field(default_factory=list)


def _check_symlink_safety(path: Path, base_dir: Path) -> None:
    """Ensure path is not an insecure symlink escaping the base_dir."""
    if path.is_symlink():
        target = path.resolve()
        base_resolved = base_dir.resolve()
        try:
            target.relative_to(base_resolved)
        except ValueError:
            raise StorageError(
                f"Insecure symlink detected at '{path}' pointing outside runtime to '{target}'"
            )


def _check_permissions(path: Path, is_dir: bool) -> None:
    """Verify that pre-existing files and directories do not have insecure group/world permissions."""
    try:
        st = path.stat()
    except OSError as e:
        raise StorageError(f"Cannot stat '{path}': {e}") from e

    mode = stat.S_IMODE(st.st_mode)

    # Check for group or others permissions (must have zero bits in 0o077)
    if (mode & 0o077) != 0:
        expected = "0700" if is_dir else "0600"
        kind = "directory" if is_dir else "file"
        raise StorageError(
            f"Insecure {kind} permissions on '{path}': {oct(mode)}, "
            f"expected {expected} with no group/world access"
        )


def _ensure_dir_secure(dir_path: Path, base_dir: Path, created_items: List[str], verified_items: List[str]) -> bool:
    """Create directory with 0700 mode if missing, or verify security if existing."""
    _check_symlink_safety(dir_path, base_dir)

    if dir_path.exists():
        if not dir_path.is_dir():
            raise StorageError(f"Path '{dir_path}' exists but is not a directory")
        _check_permissions(dir_path, is_dir=True)
        verified_items.append(str(dir_path))
        return False

    old_umask = os.umask(0o077)
    try:
        dir_path.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            os.chmod(dir_path, 0o700)
        except OSError:
            pass
        created_items.append(str(dir_path))
        return True
    except OSError as e:
        raise StorageError(f"Failed to create directory '{dir_path}': {e}") from e
    finally:
        os.umask(old_umask)


def _init_file_if_missing(
    target_file: Path,
    base_dir: Path,
    initial_content: str,
    lock_file: Path,
    created_items: List[str],
    verified_items: List[str],
    validator_fn: Callable[[Path], None] | None = None,
    timeout_seconds: float = 10.0,
) -> bool:
    """Initialize a file atomically if missing, or verify it if pre-existing."""
    _check_symlink_safety(target_file, base_dir)

    if target_file.exists():
        if not target_file.is_file():
            raise StorageError(f"Path '{target_file}' exists but is not a regular file")
        _check_permissions(target_file, is_dir=False)
        if validator_fn is not None:
            validator_fn(target_file)
        verified_items.append(str(target_file))
        return False

    # Synchronize creation via lockfile
    lock_fd = None
    try:
        lock_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)

        deadline = time.monotonic() + timeout_seconds
        delay = 0.001
        acquired = False
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
            raise StorageError(f"Timed out acquiring lock on '{lock_file}' for initializing '{target_file}'")

        # Re-check under lock
        if target_file.exists():
            _check_permissions(target_file, is_dir=False)
            if validator_fn is not None:
                validator_fn(target_file)
            verified_items.append(str(target_file))
            return False

        tmp_file = target_file.with_name(f"{target_file.name}.tmp.{os.getpid()}_{time.monotonic_ns()}")
        try:
            tmp_fd = os.open(tmp_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with open(tmp_fd, "w", encoding="utf-8") as f:
                if initial_content:
                    f.write(initial_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, target_file)
            created_items.append(str(target_file))
            return True
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
    except OSError as e:
        if isinstance(e, StorageError):
            raise
        raise StorageError(f"Failed to initialize file '{target_file}': {e}") from e
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


def _init_json_if_missing(
    target_file: Path,
    base_dir: Path,
    default_factory: Callable[[], Dict[str, Any]],
    validator: Callable[[Dict[str, Any]], None],
    lock_file: Path,
    created_items: List[str],
    verified_items: List[str],
    timeout_seconds: float = 10.0,
) -> bool:
    """Initialize a JSON config file atomically if missing, or validate if pre-existing."""
    _check_symlink_safety(target_file, base_dir)

    if target_file.exists():
        if not target_file.is_file():
            raise StorageError(f"Path '{target_file}' exists but is not a regular file")
        _check_permissions(target_file, is_dir=False)
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            validator(data)
        except Exception as e:
            if isinstance(e, StorageError):
                raise
            raise StorageError(f"Invalid configuration in '{target_file}': {e}") from e
        verified_items.append(str(target_file))
        return False

    lock_fd = None
    try:
        lock_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)

        deadline = time.monotonic() + timeout_seconds
        delay = 0.001
        acquired = False
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
            raise StorageError(f"Timed out acquiring lock on '{lock_file}' for initializing '{target_file}'")

        if target_file.exists():
            _check_permissions(target_file, is_dir=False)
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            validator(data)
            verified_items.append(str(target_file))
            return False

        default_data = default_factory()
        validator(default_data)

        tmp_file = target_file.with_name(f"{target_file.name}.tmp.{os.getpid()}_{time.monotonic_ns()}")
        serialized = json.dumps(default_data, ensure_ascii=False, indent=2) + "\n"
        try:
            tmp_fd = os.open(tmp_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with open(tmp_fd, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, target_file)
            created_items.append(str(target_file))
            return True
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
    except Exception as e:
        if isinstance(e, StorageError):
            raise
        raise StorageError(f"Failed to initialize JSON file '{target_file}': {e}") from e
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


def _validate_vault_file(vault_file: Path) -> None:
    """Validate that pre-existing vault lines match Event Schema v1 without non-JSON headers."""
    try:
        with open(vault_file, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f, start=1):
                clean = line.strip()
                if not clean:
                    continue
                try:
                    payload = json.loads(clean)
                    Event.from_dict(payload)
                except Exception as e:
                    raise StorageError(
                        f"Vault file '{vault_file}' contains invalid event at line {line_idx}: {e}"
                    ) from e
    except OSError as e:
        raise StorageError(f"Cannot read vault file '{vault_file}': {e}") from e


def ensure_user_runtime(paths: SiegfriedPaths | None = None) -> RuntimeInitResult:
    """Initialize or verify the user runtime environment securely and idempotently."""
    paths = paths or default_paths
    base_dir = paths.base_dir
    created_items: List[str] = []
    verified_items: List[str] = []

    # 1. Base directory
    _check_symlink_safety(base_dir, base_dir)
    _ensure_dir_secure(base_dir, base_dir, created_items, verified_items)

    # 2. Subdirectories
    directories = [
        paths.config_dir,
        paths.data_dir,
        paths.assets_dir,
        paths.sounds_dir,
        paths.logs_dir,
    ]
    for d in directories:
        _ensure_dir_secure(d, base_dir, created_items, verified_items)

    # 3. Config files
    # secrets.env
    _init_file_if_missing(
        target_file=paths.secrets_file,
        base_dir=base_dir,
        initial_content=SECRETS_ENV_TEMPLATE,
        lock_file=paths.config_dir / "secrets.lock",
        created_items=created_items,
        verified_items=verified_items,
    )

    # core_profile.json
    _init_json_if_missing(
        target_file=paths.core_profile_file,
        base_dir=base_dir,
        default_factory=get_default_core_profile,
        validator=validate_core_profile,
        lock_file=paths.config_dir / "core_profile.lock",
        created_items=created_items,
        verified_items=verified_items,
    )

    # active_agenda.json
    _init_json_if_missing(
        target_file=paths.active_agenda_file,
        base_dir=base_dir,
        default_factory=get_default_active_agenda,
        validator=validate_active_agenda,
        lock_file=paths.active_agenda_lock_file,
        created_items=created_items,
        verified_items=verified_items,
    )

    # 4. Data files
    # siegfried_vault.jsonl (empty pure JSONL)
    _init_file_if_missing(
        target_file=paths.vault_file,
        base_dir=base_dir,
        initial_content="",
        lock_file=paths.data_dir / "vault.lock",
        created_items=created_items,
        verified_items=verified_items,
        validator_fn=_validate_vault_file,
    )

    # .history (empty REPL history)
    _init_file_if_missing(
        target_file=paths.history_file,
        base_dir=base_dir,
        initial_content="",
        lock_file=paths.data_dir / "history.lock",
        created_items=created_items,
        verified_items=verified_items,
    )

    was_created = len(created_items) > 0
    return RuntimeInitResult(
        base_dir=base_dir,
        created=was_created,
        status="READY",
        created_items=created_items,
        verified_items=verified_items,
    )
