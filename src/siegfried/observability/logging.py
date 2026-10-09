"""Structured logging for Siegfried without PyPI dependencies.

CRITICAL RULES:
1. Structured log lines: timestamp, component, level, message, optional data.
2. NEVER log secrets, API keys, private window titles or full prompts.
"""

import json
import logging
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict


# Custom JSON-like formatter for structured output
class StructuredFormatter(logging.Formatter):
    """Formats log records as compact structured text or JSON lines."""

    def __init__(self, json_output: bool = False) -> None:
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        data = getattr(record, "structured_data", {})
        # Redact any obvious sensitive keys if mistakenly passed
        sanitized_data = self._sanitize(data)
        message = self._sanitize_text(record.getMessage())

        if self.json_output:
            log_entry = {
                "ts": record.created,
                "lvl": record.levelname,
                "cmp": record.name,
                "msg": message,
            }
            if sanitized_data:
                log_entry["data"] = sanitized_data
            return json.dumps(log_entry, ensure_ascii=False)
        else:
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
            extra_str = f" {sanitized_data}" if sanitized_data else ""
            return f"[{time_str}] [{record.levelname:<5}] [{record.name}] {message}{extra_str}"

    @staticmethod
    def _sanitize_text(text: str) -> str:
        text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
                      "[REDACTED]", text, flags=re.DOTALL)
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
        text = re.sub(r"\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
        text = re.sub(r"\b[A-Za-z0-9_]*(?:api[_-]?key|secret|token|password|authorization|credential|"
                      r"prompt|content|task[_ -]?name|window[_ -]?(?:title|caption))['\"]?\s*[:=].*",
                      "[REDACTED]", text, flags=re.IGNORECASE | re.DOTALL)
        # One record per line, including values passed through structured data.
        return text.replace("\r", "\\r").replace("\n", "\\n").replace("\x00", "\\0")

    @staticmethod
    def _sanitize(data: Any) -> Any:
        if isinstance(data, str):
            return StructuredFormatter._sanitize_text(data)
        if isinstance(data, (list, tuple)):
            return [StructuredFormatter._sanitize(value) for value in data]
        if not isinstance(data, dict):
            return data
        clean = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if any(s in key_lower for s in ("secret", "token", "password", "key", "auth", "credential",
                                            "prompt", "content", "task", "title", "caption", "url",
                                            "document", "history", "vault", "agenda", "profile", "private")):
                clean[k] = "[REDACTED]"
            else:
                clean[k] = StructuredFormatter._sanitize(v)
        return clean


class _PrivateFileHandler(logging.FileHandler):
    """Append only to an owned 0600 regular file in a real private directory."""

    def _open(self):
        from siegfried.integrations.briefing_files import directory_fd
        path = Path(self.baseFilename)
        directory = directory_fd(path.parent, private=True)
        fd = None
        try:
            fd = os.open(path.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                         os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("insecure_log_file")
            stream = os.fdopen(fd, "a", encoding=self.encoding, errors=self.errors)
            fd = None
            return stream
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)


def setup_logger(
    component: str,
    log_dir: Path | None = None,
    level: int = logging.INFO,
    json_output: bool = False
) -> logging.Logger:
    """Configure a structured logger for a component."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", component):
        raise ValueError("invalid_log_component")
    logger = logging.getLogger(f"siegfried.{component}")
    logger.setLevel(level)
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:
            pass
    logger.handlers.clear()
    logger.propagate = False

    formatter = StructuredFormatter(json_output=json_output)

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler if log_dir provided
    if log_dir is not None:
        try:
            log_dir.mkdir(mode=0o700, exist_ok=True)
            file_path = log_dir / f"{component}.log"
            file_handler = _PrivateFileHandler(str(file_path), encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except (OSError, ValueError):
            logger.warning("Archivo de log privado no disponible.")

    return logger
