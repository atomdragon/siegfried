"""IPC Protocol v1 contract definitions and serialization."""

from enum import Enum
from typing import Any, Dict, NamedTuple
import os


IPC_PROTOCOL_VERSION: int = 1


class IPCCommand(str, Enum):
    """Supported IPC command verbs between CLI and daemon."""
    PING = "PING"
    STATUS = "STATUS"
    START_FOCUS = "START_FOCUS"
    CANCEL_FOCUS = "CANCEL_FOCUS"
    POSTPONE = "POSTPONE"
    ACK_BREAK = "ACK_BREAK"
    MANUAL_SLEEP = "MANUAL_SLEEP"
    SHUTDOWN = "SHUTDOWN"
    QUERY = "QUERY"


class IPCStatus(str, Enum):
    """Response status codes."""
    OK = "OK"
    ERROR = "ERROR"
    REJECTED = "REJECTED"


class IPCRequest(NamedTuple):
    """IPC request message structure."""
    v: int
    cmd: str
    args: Dict[str, Any]
    request_id: str

    @classmethod
    def create(cls, cmd: IPCCommand | str, args: Dict[str, Any] | None = None, request_id: str | None = None) -> "IPCRequest":
        cmd_str = cmd.value if isinstance(cmd, IPCCommand) else str(cmd)
        return cls(
            v=IPC_PROTOCOL_VERSION,
            cmd=cmd_str,
            args=dict(args or {}),
            request_id=request_id or os.urandom(4).hex()
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": self.v,
            "cmd": self.cmd,
            "args": self.args,
            "request_id": self.request_id
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "IPCRequest":
        if not isinstance(d, dict):
            raise ValueError(f"IPC Request must be a JSON object, got {type(d)}")
        v = d.get("v")
        if v != IPC_PROTOCOL_VERSION:
            raise ValueError(f"Unsupported IPC protocol version: {v} (expected {IPC_PROTOCOL_VERSION})")
        cmd = d.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            raise ValueError("IPC Request 'cmd' must be a non-empty string")
        args = d.get("args", {})
        if not isinstance(args, dict):
            raise ValueError(f"IPC Request 'args' must be a dictionary, got {type(args)}")
        req_id = d.get("request_id", "")
        return cls(v=v, cmd=cmd, args=args, request_id=str(req_id))


class IPCResponse(NamedTuple):
    """IPC response message structure."""
    v: int
    request_id: str
    status: str
    payload: Dict[str, Any]
    error_msg: str | None = None

    @classmethod
    def ok(cls, request_id: str, payload: Dict[str, Any] | None = None) -> "IPCResponse":
        return cls(
            v=IPC_PROTOCOL_VERSION,
            request_id=request_id,
            status=IPCStatus.OK.value,
            payload=dict(payload or {}),
            error_msg=None
        )

    @classmethod
    def error(cls, request_id: str, error_msg: str, payload: Dict[str, Any] | None = None) -> "IPCResponse":
        return cls(
            v=IPC_PROTOCOL_VERSION,
            request_id=request_id,
            status=IPCStatus.ERROR.value,
            payload=dict(payload or {}),
            error_msg=error_msg
        )

    @classmethod
    def rejected(cls, request_id: str, reason: str, payload: Dict[str, Any] | None = None) -> "IPCResponse":
        return cls(
            v=IPC_PROTOCOL_VERSION,
            request_id=request_id,
            status=IPCStatus.REJECTED.value,
            payload=dict(payload or {}),
            error_msg=reason
        )

    @classmethod
    def busy(
        cls,
        request_id: str,
        reason: str = "El motor de inferencia está ocupado. Inténtalo nuevamente.",
        payload: Dict[str, Any] | None = None,
    ) -> "IPCResponse":
        p = dict(payload or {})
        p.setdefault("code", "INFERENCE_BUSY")
        p.setdefault("error_code", "INFERENCE_BUSY")
        p.setdefault("success", False)
        return cls(
            v=IPC_PROTOCOL_VERSION,
            request_id=request_id,
            status=IPCStatus.REJECTED.value,
            payload=p,
            error_msg=reason,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": self.v,
            "request_id": self.request_id,
            "status": self.status,
            "payload": self.payload,
            "error_msg": self.error_msg
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "IPCResponse":
        if not isinstance(d, dict):
            raise ValueError(f"IPC Response must be a JSON object, got {type(d)}")
        v = d.get("v")
        if v != IPC_PROTOCOL_VERSION:
            raise ValueError(f"Unsupported IPC protocol version: {v} (expected {IPC_PROTOCOL_VERSION})")
        req_id = d.get("request_id", "")
        status = d.get("status")
        if not isinstance(status, str):
            raise ValueError("IPC Response 'status' must be a string")
        payload = d.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("IPC Response 'payload' must be an object")
        err_msg = d.get("error_msg")
        return cls(v=v, request_id=str(req_id), status=status, payload=payload, error_msg=err_msg)
