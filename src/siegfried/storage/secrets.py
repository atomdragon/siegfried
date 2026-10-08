"""Secure secrets loading and privacy-preserving credential management for Siegfried."""

import os
from pathlib import Path
import stat
from typing import Dict, Optional

from siegfried.core.errors import (
    InsecurePermissionsError,
    InvalidConfigError,
    StorageError,
    UnsafePathError,
)
from siegfried.storage.paths import SiegfriedPaths, default_paths


def load_secrets(
    secrets_file: Path | None = None,
    base_dir: Path | None = None,
    strict_permissions: bool = True,
) -> Dict[str, str]:
    """Securely load key-value secrets from secrets.env without shell evaluation.

    Args:
        secrets_file: Specific path to secrets.env file. If None, uses default_paths.secrets_file.
        base_dir: Base directory for symlink boundary validation. If None, uses default_paths.base_dir.
        strict_permissions: If True, enforces strict 0600 POSIX permissions.

    Returns:
        Dictionary mapping secret keys to their string values.

    Raises:
        InsecurePermissionsError: If secrets.env permissions are too open.
        UnsafePathError: If secrets.env is an insecure symlink or special file.
        InvalidConfigError: If secrets.env contains invalid UTF-8.
        StorageError: On I/O or access errors.
    """
    if secrets_file is None:
        paths = default_paths
        target = paths.secrets_file
        base = paths.base_dir
    else:
        target = Path(secrets_file)
        base = Path(base_dir) if base_dir is not None else target.parent.parent

    if not target.exists():
        return {}

    # 1. Symlink and special file safety checks
    try:
        st_lstat = os.lstat(target)
    except OSError as e:
        raise StorageError(f"Error accessing secrets file: {e}") from e

    if stat.S_ISLNK(st_lstat.st_mode):
        resolved_target = target.resolve()
        resolved_base = base.resolve()
        try:
            resolved_target.relative_to(resolved_base)
        except ValueError:
            raise UnsafePathError("Insecure symlink detected on secrets file pointing outside runtime")

    try:
        st = os.stat(target)
    except OSError as e:
        raise StorageError(f"Error checking secrets file mode: {e}") from e

    if not stat.S_ISREG(st.st_mode):
        raise UnsafePathError("Secrets file is not a regular file")

    # 2. Strict POSIX permissions (0600)
    if strict_permissions:
        mode = stat.S_IMODE(st.st_mode)
        if (mode & 0o077) != 0:
            raise InsecurePermissionsError(
                f"Insecure permissions on secrets file: {oct(mode)}, expected 0600 with no group/world access"
            )
        if (mode & 0o600) != 0o600:
            raise InsecurePermissionsError(
                f"Insufficient permissions on secrets file: {oct(mode)}, expected 0600"
            )

    # 3. Read content safely without symlink following
    fd = None
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with open(fd, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError as e:
        raise InvalidConfigError(f"Secrets file '{target.name}' contains invalid UTF-8 encoding") from e
    except OSError as e:
        raise StorageError(f"I/O error reading secrets file: {e}") from e
    finally:
        # fd is closed by python's open() context manager, but if open() failed, close fd
        pass

    # 4. Parse KEY=VALUE lines
    secrets: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        # Strip optional enclosing quotes
        if len(val) >= 2 and ((val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'"))):
            val = val[1:-1]
        if key:
            secrets[key] = val

    return secrets


def get_secret(
    key: str,
    secrets_file: Path | None = None,
    fallback_env: bool = True,
    default: Optional[str] = None,
) -> Optional[str]:
    """Retrieve a single secret by key from secrets.env with optional environment fallback."""
    try:
        secrets = load_secrets(secrets_file=secrets_file)
        if key in secrets and secrets[key]:
            return secrets[key]
    except Exception:
        pass

    if fallback_env:
        env_val = os.environ.get(key)
        if env_val is not None and env_val.strip():
            return env_val.strip()

    return default
