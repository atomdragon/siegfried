"""Unix Domain Socket client for communicating with siegfried-daemon."""

import socket
from pathlib import Path
from typing import Any, Dict
from siegfried.contracts.ipc import IPCRequest, IPCResponse, IPCCommand
from siegfried.core.errors import IPCCommunicationError
from siegfried.ipc.protocol import serialize_frame, deserialize_frame


class IPCClient:
    """Client for synchronous request-response interactions with the daemon."""

    def __init__(self, socket_path: Path, timeout_seconds: float = 2.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout_seconds = timeout_seconds

    def send_request(self, request: IPCRequest, timeout_seconds: float | None = None) -> IPCResponse:
        """Send a typed IPCRequest and return an IPCResponse."""
        if not self.socket_path.exists():
            raise IPCCommunicationError(f"Daemon socket does not exist at {self.socket_path}")

        eff_timeout = float(timeout_seconds) if timeout_seconds is not None else self.timeout_seconds
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(eff_timeout)
        try:
            sock.connect(str(self.socket_path))
            # Send serialized request frame
            payload_bytes = serialize_frame(request.to_dict())
            sock.sendall(payload_bytes)

            # Read response until newline
            buffer = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                if b"\n" in chunk:
                    break

            if not buffer:
                raise IPCCommunicationError("No response received from daemon (connection closed)")

            data = deserialize_frame(bytes(buffer))
            return IPCResponse.from_dict(data)

        except (socket.error, OSError) as e:
            raise IPCCommunicationError(f"IPC communication error: {e}") from e
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def call(
        self,
        cmd: IPCCommand | str,
        args: Dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> IPCResponse:
        """Helper to invoke a command directly."""
        req = IPCRequest.create(cmd=cmd, args=args)
        return self.send_request(req, timeout_seconds=timeout_seconds)
