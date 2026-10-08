"""Unix Domain Socket server using standard library selectors."""

import os
import selectors
import socket
from pathlib import Path
from typing import Callable, Dict, Any
from siegfried.contracts.ipc import IPCRequest, IPCResponse
from siegfried.ipc.protocol import serialize_frame, deserialize_frame


CommandHandler = Callable[[IPCRequest], IPCResponse]


class IPCServer:
    """Non-blocking Unix Domain Socket server managing incoming CLI client connections."""

    def __init__(self, socket_path: Path, handler: CommandHandler) -> None:
        self.socket_path = Path(socket_path)
        self.handler = handler
        self._server_sock: socket.socket | None = None
        self._selector: selectors.DefaultSelector | None = None
        self._buffers: Dict[socket.socket, bytearray] = {}

    def start(self) -> None:
        """Bind socket and prepare listening selector."""
        # Unlink existing stale socket if it exists
        if self.socket_path.exists():
            # Test if a process is actually alive on this socket
            test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                test_sock.connect(str(self.socket_path))
                # Connection succeeded — another daemon is running
                raise OSError(
                    f"Socket {self.socket_path} is already in use by another running daemon process"
                )
            except ConnectionRefusedError:
                # Stale socket from dead process: clean it up
                self.socket_path.unlink()
            except FileNotFoundError:
                # Socket file was removed between exists() check and connect()
                pass
            finally:
                test_sock.close()

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(str(self.socket_path))
        self._server_sock.listen(16)
        self._server_sock.setblocking(False)

        # Restrict socket permissions strictly to current user
        os.chmod(self.socket_path, 0o600)

        self._selector = selectors.DefaultSelector()
        self._selector.register(self._server_sock, selectors.EVENT_READ, data=self._accept)

    def poll(self, timeout_seconds: float = 0.05) -> None:
        """Process pending I/O events with specified timeout."""
        if self._selector is None:
            return

        events = self._selector.select(timeout=timeout_seconds)
        for key, mask in events:
            callback = key.data
            callback(key.fileobj, mask)

    def _accept(self, sock: socket.socket, mask: int) -> None:
        """Accept new client connection."""
        try:
            conn, _ = sock.accept()
        except (BlockingIOError, InterruptedError):
            return
        conn.setblocking(False)
        self._buffers[conn] = bytearray()
        if self._selector:
            self._selector.register(conn, selectors.EVENT_READ, data=self._read_client)

    def _read_client(self, conn: socket.socket, mask: int) -> None:
        """Read frame from client connection, dispatch handler, and send response."""
        buffer = self._buffers.get(conn)
        if buffer is None:
            return

        try:
            chunk = conn.recv(4096)
            if not chunk:
                self._close_client(conn)
                return

            buffer.extend(chunk)
            if b"\n" in buffer:
                # Delimited frame received
                frame_data = bytes(buffer)
                try:
                    req_dict = deserialize_frame(frame_data)
                    req = IPCRequest.from_dict(req_dict)
                    res = self.handler(req)
                except Exception as e:
                    res = IPCResponse.error(
                        request_id=getattr(req, "request_id", "") if "req" in locals() else "",
                        error_msg=f"Bad request: {e}"
                    )

                resp_bytes = serialize_frame(res.to_dict())
                conn.sendall(resp_bytes)
                self._close_client(conn)

        except (socket.error, OSError):
            self._close_client(conn)

    def _close_client(self, conn: socket.socket) -> None:
        """Unregister and close client socket."""
        if self._selector:
            try:
                self._selector.unregister(conn)
            except (KeyError, ValueError):
                pass
        self._buffers.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass

    def stop(self) -> None:
        """Stop server, close sockets and clean up socket file."""
        if self._selector:
            try:
                self._selector.close()
            except Exception:
                pass
            self._selector = None

        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
