"""Structured logging for Siegfried without PyPI dependencies.

CRITICAL RULES:
1. Structured log lines: timestamp, component, level, message, optional data.
2. NEVER log secrets, API keys, private window titles or full prompts.
"""

import json
import logging
import os
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

        if self.json_output:
            log_entry = {
                "ts": record.created,
                "lvl": record.levelname,
                "cmp": record.name,
                "msg": record.getMessage(),
            }
            if sanitized_data:
                log_entry["data"] = sanitized_data
            return json.dumps(log_entry, ensure_ascii=False)
        else:
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
            extra_str = f" {sanitized_data}" if sanitized_data else ""
            return f"[{time_str}] [{record.levelname:<5}] [{record.name}] {record.getMessage()}{extra_str}"

    @staticmethod
    def _sanitize(data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        clean = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if any(s in key_lower for s in ("secret", "token", "password", "key", "auth", "credential")):
                clean[k] = "[REDACTED]"
            elif isinstance(v, dict):
                clean[k] = StructuredFormatter._sanitize(v)
            else:
                clean[k] = v
        return clean


def setup_logger(
    component: str,
    log_dir: Path | None = None,
    level: int = logging.INFO,
    json_output: bool = False
) -> logging.Logger:
    """Configure a structured logger for a component."""
    logger = logging.getLogger(f"siegfried.{component}")
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
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler if log_dir provided
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_path = log_dir / f"{component}.log"
            file_handler = logging.FileHandler(str(file_path), encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            pass

    return logger
