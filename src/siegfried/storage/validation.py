"""Runtime validation, security audit and diagnostics for Siegfried.

Validates:
1. Runtime existence and directory scaffolding.
2. POSIX permissions (0700 for directories, 0600 for files, zero group/world bits).
3. Symlink safety and canonical path resolution (preventing traversal escapes).
4. File type integrity (rejecting FIFOs, character/block devices, sockets or directory substitutions).
5. Config Schema v1 compliance for core_profile.json and active_agenda.json.
6. UTF-8 and integrity of secrets.env without exposure.
7. Event Schema v1 integrity of siegfried_vault.jsonl (fast check vs deep audit).
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Dict, List, Optional

from siegfried.contracts.config import validate_active_agenda, validate_core_profile
from siegfried.contracts.events import Event
from siegfried.core.errors import (
    InsecurePermissionsError,
    InvalidConfigError,
    RuntimeNotInitializedError,
    StorageError,
    UnsafePathError,
)
from siegfried.storage.paths import SiegfriedPaths, default_paths


class RuntimeStatus(str, Enum):
    READY = "READY"
    NOT_INITIALIZED = "NOT_INITIALIZED"
    INVALID_CONFIG = "INVALID_CONFIG"
    INSECURE_PERMISSIONS = "INSECURE_PERMISSIONS"
    UNSAFE_PATH = "UNSAFE_PATH"
    STORAGE_ERROR = "STORAGE_ERROR"


@dataclass(frozen=True)
class DiagnosticSection:
    name: str
    status: str
    details: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeValidationResult:
    base_dir: Path
    status: RuntimeStatus
    is_ready: bool
    sections: Dict[str, str] = field(default_factory=dict)
    error_message: Optional[str] = None
    problematic_path: Optional[Path] = None
    orphaned_temp_files: List[Path] = field(default_factory=list)


def _check_symlink_safety(path: Path, base_dir: Path) -> None:
    """Check that path is not a symlink escaping base_dir using lstat and canonical resolution."""
    try:
        st = os.lstat(path)
    except OSError as e:
        raise StorageError(f"No se pudo acceder a '{path}': {e}") from e

    if stat.S_ISLNK(st.st_mode):
        target = path.resolve()
        base_resolved = base_dir.resolve()
        try:
            target.relative_to(base_resolved)
        except ValueError:
            raise UnsafePathError(
                f"Enlace simbólico inseguro detectado en '{path}' apuntando fuera del runtime autorizado a '{target}'"
            )


def _check_permissions(path: Path, is_dir: bool) -> None:
    """Verify that file/directory POSIX mode conforms strictly to 0700 (dir) or 0600 (file)."""
    try:
        st = os.stat(path)
    except OSError as e:
        raise StorageError(f"No se puede obtener estado de permisos para '{path}': {e}") from e

    mode = stat.S_IMODE(st.st_mode)

    # Insecure group or world bits
    if (mode & 0o077) != 0:
        expected = "0700" if is_dir else "0600"
        kind = "directorio" if is_dir else "archivo"
        raise InsecurePermissionsError(
            f"Permisos inseguros en {kind} '{path}': {oct(mode)}, se esperaba {expected} sin acceso de grupo ni otros."
        )

    # Owner permissions
    if is_dir:
        if (mode & 0o700) != 0o700:
            raise InsecurePermissionsError(
                f"Permisos insuficientes en directorio '{path}': {oct(mode)}, se requiere 0700 (rwx para propietario)."
            )
    else:
        if (mode & 0o600) != 0o600:
            raise InsecurePermissionsError(
                f"Permisos insuficientes en archivo '{path}': {oct(mode)}, se requiere 0600 (rw para propietario)."
            )


def _verify_regular_file(path: Path) -> None:
    """Verify that path is a regular file (not directory, FIFO, socket, device)."""
    try:
        st = os.stat(path)
    except OSError as e:
        raise StorageError(f"Error al verificar archivo '{path}': {e}") from e

    if not stat.S_ISREG(st.st_mode):
        raise UnsafePathError(
            f"La ruta '{path}' no es un archivo regular (modo detectado: {oct(st.st_mode)})"
        )


def _verify_directory(path: Path) -> None:
    """Verify that path is a regular directory."""
    try:
        st = os.stat(path)
    except OSError as e:
        raise StorageError(f"Error al verificar directorio '{path}': {e}") from e

    if not stat.S_ISDIR(st.st_mode):
        raise UnsafePathError(
            f"La ruta '{path}' no es un directorio regular (modo detectado: {oct(st.st_mode)})"
        )


def _read_and_validate_json_file(file_path: Path, validator_fn: Any) -> None:
    """Open and validate JSON file against schema without following symlinks."""
    fd = None
    try:
        fd = os.open(file_path, os.O_RDONLY | os.O_NOFOLLOW)
        with open(fd, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError as e:
        raise InvalidConfigError(f"El archivo '{file_path}' contiene caracteres no UTF-8 válidos: {e}") from e
    except OSError as e:
        raise StorageError(f"Error I/O al leer '{file_path}': {e}") from e

    if not content.strip():
        raise InvalidConfigError(f"El archivo '{file_path}' está vacío; se requiere un objeto JSON válido")

    try:
        data = json.loads(content)
    except Exception as e:
        raise InvalidConfigError(f"Sintaxis JSON inválida o corrupta en '{file_path}': {e}") from e

    try:
        validator_fn(data)
    except ValueError as e:
        raise InvalidConfigError(f"Validación de esquema fallida en '{file_path}': {e}") from e


def _verify_secrets_env(secrets_file: Path) -> None:
    """Verify secrets.env readability without leaking its content."""
    fd = None
    try:
        fd = os.open(secrets_file, os.O_RDONLY | os.O_NOFOLLOW)
        with open(fd, "r", encoding="utf-8") as f:
            # Read to verify validity of UTF-8 encoding
            f.read()
    except UnicodeDecodeError as e:
        raise InvalidConfigError(f"El archivo '{secrets_file}' contiene caracteres no UTF-8 válidos") from e
    except OSError as e:
        raise StorageError(f"Error I/O al leer '{secrets_file}': {e}") from e


def _verify_vault(vault_file: Path, deep_audit: bool) -> None:
    """Verify vault file readability and Event Schema v1 format."""
    from siegfried.storage.vault import Vault
    
    if not deep_audit:
        # Fast check: sample first line if present
        fd = None
        try:
            fd = os.open(vault_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with open(fd, "r", encoding="utf-8") as f:
                first_line = f.readline()
                if first_line.strip():
                    try:
                        d = json.loads(first_line.strip())
                        Event.from_dict(d)
                    except Exception as e:
                        raise InvalidConfigError(f"Cabecera o primer evento corrupto en '{vault_file}': {e}") from e
        except UnicodeDecodeError as e:
            raise InvalidConfigError(f"El Vault '{vault_file}' contiene codificación no UTF-8") from e
        except OSError as e:
            raise StorageError(f"Error I/O al leer Vault '{vault_file}': {e}") from e
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
    else:
        vault = Vault(vault_file)
        audit_res = vault.audit()
        if not audit_res.is_clean:
            first_err = audit_res.corrupted_lines[0]
            if first_err.is_trailing_truncated:
                raise InvalidConfigError(f"Línea {first_err.line_number} del Vault '{vault_file}' está truncada o incompleta: {first_err.error}")
            elif "JSON inválido" in first_err.error or "no es JSON válido" in first_err.error:
                raise InvalidConfigError(f"Línea {first_err.line_number} del Vault '{vault_file}' no es JSON válido: {first_err.error}")
            else:
                raise InvalidConfigError(f"Evento inválido en línea {first_err.line_number} de '{vault_file}': {first_err.error}")


def validate_user_runtime(
    paths: SiegfriedPaths | None = None,
    deep_vault_audit: bool = False
) -> RuntimeValidationResult:
    """Strict, non-destructive audit of the user runtime structure, permissions, and configs."""
    paths = paths or default_paths
    base_dir = paths.base_dir

    sections = {
        "Configuración": "OK",
        "Permisos": "OK",
        "Vault": "OK",
        "Rutas": "OK",
    }

    # 1. Existence check (NOT_INITIALIZED)
    if not base_dir.exists():
        sections["Rutas"] = "NOT_INITIALIZED"
        sections["Configuración"] = "NOT_INITIALIZED"
        sections["Permisos"] = "NOT_INITIALIZED"
        sections["Vault"] = "NOT_INITIALIZED"
        return RuntimeValidationResult(
            base_dir=base_dir,
            status=RuntimeStatus.NOT_INITIALIZED,
            is_ready=False,
            sections=sections,
            error_message="Runtime no inicializado: el directorio base no existe. Ejecute 'siegfried init'.",
            problematic_path=base_dir,
        )

    mandatory_dirs = [
        paths.config_dir,
        paths.data_dir,
        paths.assets_dir,
        paths.sounds_dir,
        paths.logs_dir,
    ]
    for d in mandatory_dirs:
        if not d.exists():
            sections["Rutas"] = "NOT_INITIALIZED"
            return RuntimeValidationResult(
                base_dir=base_dir,
                status=RuntimeStatus.NOT_INITIALIZED,
                is_ready=False,
                sections=sections,
                error_message=f"Runtime no inicializado: falta el directorio '{d}'. Ejecute 'siegfried init'.",
                problematic_path=d,
            )

    mandatory_files = [
        (paths.secrets_file, "Configuración"),
        (paths.core_profile_file, "Configuración"),
        (paths.active_agenda_file, "Configuración"),
        (paths.vault_file, "Vault"),
        (paths.history_file, "Rutas"),
    ]
    for f, sec_name in mandatory_files:
        if not f.exists():
            sections[sec_name] = "NOT_INITIALIZED"
            return RuntimeValidationResult(
                base_dir=base_dir,
                status=RuntimeStatus.NOT_INITIALIZED,
                is_ready=False,
                sections=sections,
                error_message=f"Runtime no inicializado: falta el archivo '{f}'. Ejecute 'siegfried init'.",
                problematic_path=f,
            )

    # 2. Symlinks & Path Safety Check (UNSAFE_PATH)
    all_dirs = [base_dir] + mandatory_dirs
    for d in all_dirs:
        try:
            _check_symlink_safety(d, base_dir)
            _verify_directory(d)
        except UnsafePathError as e:
            sections["Rutas"] = "UNSAFE_PATH"
            return RuntimeValidationResult(
                base_dir=base_dir,
                status=RuntimeStatus.UNSAFE_PATH,
                is_ready=False,
                sections=sections,
                error_message=str(e),
                problematic_path=d,
            )
        except StorageError as e:
            sections["Rutas"] = "STORAGE_ERROR"
            return RuntimeValidationResult(
                base_dir=base_dir,
                status=RuntimeStatus.STORAGE_ERROR,
                is_ready=False,
                sections=sections,
                error_message=str(e),
                problematic_path=d,
            )

    for f, sec_name in mandatory_files:
        try:
            _check_symlink_safety(f, base_dir)
            _verify_regular_file(f)
        except UnsafePathError as e:
            sections["Rutas"] = "UNSAFE_PATH"
            sections[sec_name] = "UNSAFE_PATH"
            return RuntimeValidationResult(
                base_dir=base_dir,
                status=RuntimeStatus.UNSAFE_PATH,
                is_ready=False,
                sections=sections,
                error_message=str(e),
                problematic_path=f,
            )
        except StorageError as e:
            sections[sec_name] = "STORAGE_ERROR"
            return RuntimeValidationResult(
                base_dir=base_dir,
                status=RuntimeStatus.STORAGE_ERROR,
                is_ready=False,
                sections=sections,
                error_message=str(e),
                problematic_path=f,
            )

    # 3. Permissions Check (INSECURE_PERMISSIONS)
    for d in all_dirs:
        try:
            _check_permissions(d, is_dir=True)
        except InsecurePermissionsError as e:
            sections["Permisos"] = "INSECURE_PERMISSIONS"
            return RuntimeValidationResult(
                base_dir=base_dir,
                status=RuntimeStatus.INSECURE_PERMISSIONS,
                is_ready=False,
                sections=sections,
                error_message=str(e),
                problematic_path=d,
            )

    for f, sec_name in mandatory_files:
        try:
            _check_permissions(f, is_dir=False)
        except InsecurePermissionsError as e:
            sections["Permisos"] = "INSECURE_PERMISSIONS"
            return RuntimeValidationResult(
                base_dir=base_dir,
                status=RuntimeStatus.INSECURE_PERMISSIONS,
                is_ready=False,
                sections=sections,
                error_message=str(e),
                problematic_path=f,
            )

    # 4. Config & Secrets Integrity Check (INVALID_CONFIG)
    try:
        _verify_secrets_env(paths.secrets_file)
    except InvalidConfigError as e:
        sections["Configuración"] = "INVALID_CONFIG"
        return RuntimeValidationResult(
            base_dir=base_dir,
            status=RuntimeStatus.INVALID_CONFIG,
            is_ready=False,
            sections=sections,
            error_message=str(e),
            problematic_path=paths.secrets_file,
        )

    try:
        _read_and_validate_json_file(paths.core_profile_file, validate_core_profile)
    except InvalidConfigError as e:
        sections["Configuración"] = "INVALID_CONFIG"
        return RuntimeValidationResult(
            base_dir=base_dir,
            status=RuntimeStatus.INVALID_CONFIG,
            is_ready=False,
            sections=sections,
            error_message=str(e),
            problematic_path=paths.core_profile_file,
        )

    try:
        _read_and_validate_json_file(paths.active_agenda_file, validate_active_agenda)
    except InvalidConfigError as e:
        sections["Configuración"] = "INVALID_CONFIG"
        return RuntimeValidationResult(
            base_dir=base_dir,
            status=RuntimeStatus.INVALID_CONFIG,
            is_ready=False,
            sections=sections,
            error_message=str(e),
            problematic_path=paths.active_agenda_file,
        )

    # 5. Vault Integrity Check
    try:
        _verify_vault(paths.vault_file, deep_audit=deep_vault_audit)
    except InvalidConfigError as e:
        sections["Vault"] = "INVALID_CONFIG"
        return RuntimeValidationResult(
            base_dir=base_dir,
            status=RuntimeStatus.INVALID_CONFIG,
            is_ready=False,
            sections=sections,
            error_message=str(e),
            problematic_path=paths.vault_file,
        )
    except StorageError as e:
        sections["Vault"] = "STORAGE_ERROR"
        return RuntimeValidationResult(
            base_dir=base_dir,
            status=RuntimeStatus.STORAGE_ERROR,
            is_ready=False,
            sections=sections,
            error_message=str(e),
            problematic_path=paths.vault_file,
        )

    # 6. Orphaned temporary files scan (non-destructive)
    orphaned_temps: List[Path] = []
    for d in [paths.config_dir, paths.data_dir]:
        if d.exists() and d.is_dir():
            try:
                for entry in d.iterdir():
                    if ".tmp." in entry.name or entry.name.endswith(".tmp"):
                        orphaned_temps.append(entry)
            except OSError:
                pass
    orphaned_temps.sort()

    # 7. All OK -> READY
    return RuntimeValidationResult(
        base_dir=base_dir,
        status=RuntimeStatus.READY,
        is_ready=True,
        sections=sections,
        error_message=None,
        problematic_path=None,
        orphaned_temp_files=orphaned_temps,
    )


def raise_if_runtime_invalid(
    paths: SiegfriedPaths | None = None,
    deep_vault_audit: bool = False
) -> None:
    """Helper that runs validate_user_runtime and raises corresponding typed StorageError if not ready."""
    result = validate_user_runtime(paths=paths, deep_vault_audit=deep_vault_audit)
    if not result.is_ready:
        msg = result.error_message or f"Runtime validation failed with status {result.status.value}"
        if result.status == RuntimeStatus.NOT_INITIALIZED:
            raise RuntimeNotInitializedError(msg)
        elif result.status == RuntimeStatus.INVALID_CONFIG:
            raise InvalidConfigError(msg)
        elif result.status == RuntimeStatus.INSECURE_PERMISSIONS:
            raise InsecurePermissionsError(msg)
        elif result.status == RuntimeStatus.UNSAFE_PATH:
            raise UnsafePathError(msg)
        else:
            raise StorageError(msg)
