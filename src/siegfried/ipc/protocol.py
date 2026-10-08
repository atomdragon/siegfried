"""Low-level protocol framing for Unix Domain Socket communication.

Uses newline-delimited JSON messages (`\\n`).
Each frame is a complete valid JSON object followed by newline.
"""

import json
from typing import Any, Dict
from siegfried.contracts.ipc import IPCRequest, IPCResponse
from siegfried.core.errors import IPCCommunicationError


MAX_FRAME_SIZE = 65536  # 64 KB safety limit to prevent memory exhaustion


def serialize_frame(data: Dict[str, Any]) -> bytes:
    """Serialize dictionary to newline-terminated UTF-8 byte frame."""
    try:
        raw = json.dumps(data, ensure_ascii=False) + "\n"
        encoded = raw.encode("utf-8")
        if len(encoded) > MAX_FRAME_SIZE:
            raise IPCCommunicationError(f"Frame exceeds maximum size limit ({len(encoded)} > {MAX_FRAME_SIZE})")
        return encoded
    except (TypeError, ValueError) as e:
        raise IPCCommunicationError(f"Serialization failed: {e}") from e


def deserialize_frame(raw_bytes: bytes) -> Dict[str, Any]:
    """Deserialize raw bytes containing a single JSON frame."""
    try:
        text = raw_bytes.decode("utf-8").strip()
        if not text:
            raise IPCCommunicationError("Empty frame received")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise IPCCommunicationError("Frame payload must be a JSON object")
        return parsed
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise IPCCommunicationError(f"Malformed frame: {e}") from e
