"""Append-only Vault storage enforcing Event Schema v1 with fcntl locking.

RULES:
1. Every line in siegfried_vault.jsonl must be valid JSON matching Event Schema v1.
2. No non-JSON headers or comments.
3. Locking is direct on the append-only file: fcntl.flock(LOCK_EX) -> append -> flush -> fsync -> unlock.
4. Preserves data integrity: never truncates, overwrites, or deletes damaged vaults automatically.
"""

from dataclasses import dataclass, field
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Iterator, List, Optional
from siegfried.contracts.events import Event, EventType
from siegfried.core.errors import StorageError


@dataclass(frozen=True)
class VaultCorruptLine:
    """Details of a single corrupted or malformed line found during Vault audit."""
    line_number: int
    raw_content: str
    error: str
    is_trailing_truncated: bool = False


@dataclass(frozen=True)
class VaultAuditResult:
    """Non-destructive audit summary of a Vault JSONL file."""
    is_clean: bool
    valid_events_count: int
    corrupted_lines: List[VaultCorruptLine] = field(default_factory=list)
    has_trailing_truncated_line: bool = False
    total_lines_read: int = 0


class Vault:
    """Manages concurrent reads, appends, and non-destructive audits of siegfried_vault.jsonl."""

    def __init__(self, vault_path: Path, corrupt_log_path: Path | None = None) -> None:
        self.vault_path = Path(vault_path)
        self.corrupt_log_path = Path(corrupt_log_path or self.vault_path.with_name("vault.corrupt.log"))
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)

    def audit(self) -> VaultAuditResult:
        """Perform a streaming, non-destructive audit of the vault file.
        
        Differentiates completely valid files, intermediate corrupted records,
        and trailing truncated lines without modifying or truncating the file.
        """
        if not self.vault_path.exists():
            return VaultAuditResult(
                is_clean=True,
                valid_events_count=0,
                corrupted_lines=[],
                has_trailing_truncated_line=False,
                total_lines_read=0,
            )

        fd = None
        corrupted: List[VaultCorruptLine] = []
        valid_count = 0
        total_lines = 0
        has_trailing_truncated = False

        try:
            fd = os.open(self.vault_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            fcntl.flock(fd, fcntl.LOCK_SH)

            with open(fd, "r", encoding="utf-8", closefd=False) as f:
                line_number = 0
                while True:
                    raw_line = f.readline()
                    if not raw_line:
                        break
                    line_number += 1
                    total_lines += 1

                    has_newline = raw_line.endswith("\n") or raw_line.endswith("\r\n")
                    clean_line = raw_line.strip()

                    # Handle blank lines
                    if not clean_line:
                        continue

                    # If line did not end with newline, it is at EOF and potentially truncated
                    is_at_eof_without_newline = not has_newline

                    try:
                        data = json.loads(clean_line)
                    except Exception as e:
                        is_trailing = is_at_eof_without_newline
                        if is_trailing:
                            has_trailing_truncated = True
                        corrupted.append(
                            VaultCorruptLine(
                                line_number=line_number,
                                raw_content=clean_line,
                                error=f"JSON inválido: {e}",
                                is_trailing_truncated=is_trailing,
                            )
                        )
                        continue

                    try:
                        Event.from_dict(data)
                        if is_at_eof_without_newline:
                            # Valid JSON but missing trailing newline at EOF
                            has_trailing_truncated = True
                            corrupted.append(
                                VaultCorruptLine(
                                    line_number=line_number,
                                    raw_content=clean_line,
                                    error="Línea final truncada o incompleta (falta salto de línea)",
                                    is_trailing_truncated=True,
                                )
                            )
                        else:
                            valid_count += 1
                    except Exception as e:
                        is_trailing = is_at_eof_without_newline
                        if is_trailing:
                            has_trailing_truncated = True
                        corrupted.append(
                            VaultCorruptLine(
                                line_number=line_number,
                                raw_content=clean_line,
                                error=f"Contrato Event Schema v1 violado: {e}",
                                is_trailing_truncated=is_trailing,
                            )
                        )

        except UnicodeDecodeError as e:
            corrupted.append(
                VaultCorruptLine(
                    line_number=total_lines + 1,
                    raw_content="<NON_UTF8_BYTES>",
                    error=f"Codificación no UTF-8 detectada: {e}",
                    is_trailing_truncated=False,
                )
            )
        except OSError as e:
            raise StorageError(f"Error I/O al auditar vault '{self.vault_path}': {e}") from e
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass

        return VaultAuditResult(
            is_clean=(len(corrupted) == 0 and not has_trailing_truncated),
            valid_events_count=valid_count,
            corrupted_lines=corrupted,
            has_trailing_truncated_line=has_trailing_truncated,
            total_lines_read=total_lines,
        )

    def append(self, event: Event, timeout_seconds: float = 10.0) -> None:
        """Atomically append a single validated event line to the vault.
        
        Guards against concatenating onto an incomplete/truncated trailing line.
        """
        serialized = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        fd = None
        try:
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.vault_path, os.O_CREAT | os.O_RDWR, 0o600)

            # Acquire exclusive non-blocking lock with deadline backoff
            acquired = False
            deadline = time.monotonic() + timeout_seconds
            delay = 0.001
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(min(delay, 0.05))
                    delay *= 1.5

            if not acquired:
                raise StorageError(f"Could not acquire exclusive lock on vault: {self.vault_path} (timed out after {timeout_seconds}s)")

            # Check if existing file has an unterminated trailing line
            file_size = os.lseek(fd, 0, os.SEEK_END)
            is_new_file = file_size == 0
            if file_size > 0:
                os.lseek(fd, file_size - 1, os.SEEK_SET)
                last_byte = os.read(fd, 1)
                if last_byte != b"\n":
                    raise StorageError(
                        f"Vault '{self.vault_path}' contains an unclosed or truncated trailing line. "
                        f"Appending is aborted to prevent data corruption."
                    )
                os.lseek(fd, 0, os.SEEK_END)

            # Write event line and persist to disk
            os.write(fd, serialized.encode("utf-8"))
            os.fsync(fd)

            # Durability: fsync directory if new file was created
            if is_new_file:
                try:
                    dir_fd = os.open(self.vault_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass

        except Exception as e:
            if isinstance(e, StorageError):
                raise
            raise StorageError(f"Failed to append to vault {self.vault_path}: {e}") from e
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass

    def read_events(self, allow_corrupt_skip: bool = False) -> Iterator[Event]:
        """Iterate over events in the vault using a shared lock.
        
        CRITICAL ARCHITECTURE RULE:
        Default mode is STRICT (allow_corrupt_skip=False) so that critical consumers
        (daemon, aggregators, calculators) NEVER silently treat a partial, corrupted
        vault read as a complete and integral history.
        Tolerant mode requires explicit enablement by setting allow_corrupt_skip=True.
        """
        if not self.vault_path.exists():
            return

        fd = None
        try:
            fd = os.open(self.vault_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            fcntl.flock(fd, fcntl.LOCK_SH)

            with open(fd, "r", encoding="utf-8", closefd=False) as f:
                for line_num, line in enumerate(f, start=1):
                    line_clean = line.strip()
                    if not line_clean:
                        continue
                    try:
                        d = json.loads(line_clean)
                        yield Event.from_dict(d)
                    except Exception as e:
                        if allow_corrupt_skip:
                            self._log_corrupt_line(line_num, line_clean, str(e))
                        else:
                            raise StorageError(f"Corrupt event at line {line_num}: {e}") from e
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass

    def read_tail(self, max_count: int = 100, allow_corrupt_skip: bool = False) -> List[Event]:
        """Read the last N events from the vault in strict mode by default."""
        events = list(self.read_events(allow_corrupt_skip=allow_corrupt_skip))
        return events[-max_count:] if max_count > 0 else events

    def _log_corrupt_line(self, line_num: int, raw_content: str, error_msg: str) -> None:
        """Quarantine corrupted lines into vault.corrupt.log with strict 0600 permissions."""
        fd = None
        try:
            fd = os.open(self.corrupt_log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            sanitized_content = raw_content[:200]
            log_entry = f"[{time.time()}] Line {line_num} error: {error_msg} | Content: {sanitized_content}\n"
            os.write(fd, log_entry.encode("utf-8"))
        except OSError:
            pass
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def audit_vault(vault_path: Path) -> VaultAuditResult:
    """Helper function to perform a non-destructive audit of a Vault file."""
    return Vault(vault_path).audit()
