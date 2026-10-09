import os
import selectors
import socket
import stat
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Any, List, Set, Optional
from siegfried.contracts.ipc import IPCRequest, IPCResponse, IPCCommand, IPCStatus
from siegfried.core.errors import InsecurePermissionsError, UnsafePathError
from siegfried.ipc.protocol import serialize_frame, deserialize_frame


CommandHandler = Callable[[IPCRequest], IPCResponse]

MAX_COGNITIVE_WORKERS: int = 3
MAX_PENDING_COGNITIVE_QUERIES: int = 0
MAX_ACCEPTED_CONNECTIONS: int = 32
MAX_FRAME_SIZE: int = 65536  # 64 KB
CLIENT_READ_TIMEOUT_SECONDS: float = 5.0


class IPCServer:
    """Non-blocking Unix Domain Socket server managing incoming CLI client connections."""

    def __init__(
        self,
        socket_path: Path,
        handler: CommandHandler,
        max_cognitive_workers: int = MAX_COGNITIVE_WORKERS,
        max_connections: int = MAX_ACCEPTED_CONNECTIONS,
        max_frame_size: int = MAX_FRAME_SIZE,
        client_read_timeout: float = CLIENT_READ_TIMEOUT_SECONDS,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.handler = handler
        self.max_cognitive_workers = int(max_cognitive_workers)
        self.max_connections = int(max_connections)
        self.max_frame_size = int(max_frame_size)
        self.client_read_timeout = float(client_read_timeout)

        self._server_sock: socket.socket | None = None
        self._selector: selectors.DefaultSelector | None = None
        self._buffers: Dict[socket.socket, bytearray] = {}
        self._conn_timestamps: Dict[socket.socket, float] = {}

        self._worker_threads: Set[threading.Thread] = set()
        self._worker_lock = threading.Lock()
        self._cognitive_semaphore = threading.BoundedSemaphore(self.max_cognitive_workers)

        self._stopping: bool = False
        self._stopped: bool = False
        self._shutdown_lock = threading.Lock()
        self._clean_shutdown: bool = True
        self._residual_workers: List[threading.Thread] = []

    @property
    def active_cognitive_workers(self) -> int:
        with self._worker_lock:
            return len(self._worker_threads)

    @property
    def is_stopping(self) -> bool:
        return self._stopping

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def is_clean_shutdown(self) -> bool:
        return self._clean_shutdown

    @property
    def residual_workers(self) -> List[threading.Thread]:
        with self._worker_lock:
            return list(self._residual_workers)

    def start(self) -> None:
        """Bind socket and prepare listening selector with strict ownership and permission enforcement."""
        with self._shutdown_lock:
            self._stopping = False
            self._stopped = False
            self._clean_shutdown = True
            self._residual_workers = []

        # 1. Validate parent directory
        parent_dir = self.socket_path.parent
        if not parent_dir.exists():
            old_umask = os.umask(0o077)
            try:
                parent_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            finally:
                os.umask(old_umask)
        else:
            try:
                parent_st = os.stat(parent_dir)
                current_uid = os.getuid() if hasattr(os, "getuid") else 1000
                if parent_st.st_uid != current_uid:
                    raise InsecurePermissionsError(
                        f"Directorio de runtime '{parent_dir}' pertenece a UID {parent_st.st_uid}, "
                        f"se esperaba UID actual {current_uid}."
                    )
                if (parent_st.st_mode & 0o022) != 0:
                    raise InsecurePermissionsError(
                        f"Permisos inseguros en directorio de socket '{parent_dir}': {oct(parent_st.st_mode)}, "
                        f"se requiere acceso privado sin permisos de grupo ni otros."
                    )
            except OSError as e:
                if isinstance(e, InsecurePermissionsError):
                    raise
                raise OSError(f"Error al verificar directorio de socket '{parent_dir}': {e}") from e

        # 2. Inspect existing file at socket_path before any action
        if os.path.lexists(self.socket_path):
            try:
                st = os.lstat(self.socket_path)
            except OSError as e:
                raise OSError(f"No se pudo acceder a '{self.socket_path}': {e}") from e

            # Symlink protection: never unlink an arbitrary symlink target
            if stat.S_ISLNK(st.st_mode):
                raise UnsafePathError(
                    f"Enlace simbólico inseguro detectado en ruta de socket '{self.socket_path}'"
                )

            # File type protection: must be a socket
            if not stat.S_ISSOCK(st.st_mode):
                raise UnsafePathError(
                    f"La ruta '{self.socket_path}' existe pero no es un socket UNIX (modo: {oct(st.st_mode)})"
                )

            # Ownership protection: must belong to current user
            current_uid = os.getuid() if hasattr(os, "getuid") else 1000
            if st.st_uid != current_uid:
                raise InsecurePermissionsError(
                    f"El socket '{self.socket_path}' pertenece al UID {st.st_uid}, no al usuario actual {current_uid}. "
                    f"Rechazando eliminación o sustitución de socket ajeno."
                )

            # Check if an active daemon is listening on the socket
            test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test_sock.settimeout(0.5)
            try:
                test_sock.connect(str(self.socket_path))
                # Connection succeeded — another daemon is running
                raise OSError(
                    f"Socket {self.socket_path} is already in use by another running daemon process"
                )
            except ConnectionRefusedError:
                # Stale socket from dead process: verified own socket, safe to unlink
                try:
                    self.socket_path.unlink()
                except OSError as e:
                    raise OSError(f"No se pudo eliminar el socket obsoleto '{self.socket_path}': {e}") from e
            except PermissionError as e:
                raise InsecurePermissionsError(
                    f"Permiso denegado al conectar al socket '{self.socket_path}': {e}"
                ) from e
            except FileNotFoundError:
                pass
            finally:
                test_sock.close()

        # 3. Create server socket with atomic private permissions
        old_umask = os.umask(0o177)
        try:
            self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server_sock.bind(str(self.socket_path))
            self._server_sock.listen(16)
            self._server_sock.setblocking(False)
        finally:
            os.umask(old_umask)

        try:
            os.chmod(self.socket_path, 0o600)
        except OSError:
            pass

        self._selector = selectors.DefaultSelector()
        self._selector.register(self._server_sock, selectors.EVENT_READ, data=self._accept)

    def poll(self, timeout_seconds: float = 0.05) -> None:
        """Process pending I/O events with specified timeout."""
        if self._selector is None or self._stopped:
            return

        # Check for stalled / slow incoming connections
        now = time.monotonic()
        for conn, start_time in list(self._conn_timestamps.items()):
            if now - start_time > self.client_read_timeout:
                self._close_client(conn)

        try:
            events = self._selector.select(timeout=timeout_seconds)
        except (ValueError, OSError):
            return

        for key, mask in events:
            callback = key.data
            try:
                callback(key.fileobj, mask)
            except Exception:
                pass

    def _accept(self, sock: socket.socket, mask: int) -> None:
        """Accept new client connection."""
        if self._stopping or self._stopped:
            return
        try:
            conn, _ = sock.accept()
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            return

        # Enforce connection ceiling
        if len(self._buffers) >= self.max_connections:
            try:
                conn.close()
            except OSError:
                pass
            return

        conn.setblocking(False)
        self._buffers[conn] = bytearray()
        self._conn_timestamps[conn] = time.monotonic()
        if self._selector:
            try:
                self._selector.register(conn, selectors.EVENT_READ, data=self._read_client)
            except Exception:
                self._close_client(conn)

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

            # Max frame size guard (prevent unbounded buffer expansion)
            if len(buffer) > self.max_frame_size:
                res = IPCResponse.rejected(
                    request_id="",
                    reason="Tamaño de mensaje IPC excede el límite máximo permitido (64 KB).",
                    payload={"code": "FRAME_TOO_LARGE"}
                )
                try:
                    conn.sendall(serialize_frame(res.to_dict()))
                except OSError:
                    pass
                self._close_client(conn)
                return

            if b"\n" in buffer:
                # Delimited frame received
                frame_data = bytes(buffer)
                try:
                    req_dict = deserialize_frame(frame_data)
                    req = IPCRequest.from_dict(req_dict)
                except Exception as e:
                    res = IPCResponse.error(
                        request_id="",
                        error_msg=f"Bad request: {e}"
                    )
                    resp_bytes = serialize_frame(res.to_dict())
                    try:
                        conn.sendall(resp_bytes)
                    except OSError:
                        pass
                    self._close_client(conn)
                    return

                # If server is shutting down, reject any new request immediately
                if self._stopping:
                    res = IPCResponse.rejected(
                        request_id=req.request_id,
                        reason="Servidor deteniéndose; no se admiten nuevas solicitudes.",
                        payload={"code": "SERVER_STOPPING"}
                    )
                    try:
                        conn.sendall(serialize_frame(res.to_dict()))
                    except OSError:
                        pass
                    self._close_client(conn)
                    return

                if req.cmd == IPCCommand.QUERY.value:
                    # Non-blocking capacity check: reserve slot before launching worker
                    acquired = self._cognitive_semaphore.acquire(blocking=False)
                    if not acquired:
                        # CAPACITY SATURATED: Apply immediate backpressure without worker thread
                        res = IPCResponse.busy(
                            request_id=req.request_id,
                            reason="El motor de inferencia está ocupado. Inténtalo nuevamente.",
                        )
                        resp_bytes = serialize_frame(res.to_dict())
                        try:
                            conn.sendall(resp_bytes)
                        except OSError:
                            pass
                        self._close_client(conn)
                        return

                    # Slot acquired: unregister from selector before launching worker
                    if self._selector:
                        try:
                            self._selector.unregister(conn)
                        except (KeyError, ValueError, OSError):
                            pass
                    self._buffers.pop(conn, None)
                    self._conn_timestamps.pop(conn, None)

                    worker = threading.Thread(
                        target=self._handle_async_client,
                        args=(conn, req, acquired),
                        daemon=False,
                        name=f"ipc-query-{req.request_id}",
                    )
                    try:
                        with self._worker_lock:
                            if self._stopping:
                                raise RuntimeError("Server is stopping")
                            self._worker_threads.add(worker)
                        worker.start()
                    except Exception as e:
                        with self._worker_lock:
                            self._worker_threads.discard(worker)
                        # Release acquired slot immediately on worker spawn failure
                        self._cognitive_semaphore.release()
                        res = IPCResponse.error(
                            request_id=req.request_id,
                            error_msg=f"Error al iniciar trabajador de inferencia: {e}"
                        )
                        try:
                            conn.sendall(serialize_frame(res.to_dict()))
                        except OSError:
                            pass
                        try:
                            conn.close()
                        except OSError:
                            pass
                    return

                # Fast-path determinista: execute synchronously on reactor
                res = self.handler(req)
                resp_bytes = serialize_frame(res.to_dict())
                conn.sendall(resp_bytes)
                self._close_client(conn)

        except (socket.error, OSError):
            self._close_client(conn)

    def _handle_async_client(self, conn: socket.socket, req: IPCRequest, acquired: bool) -> None:
        """Process long-running requests asynchronously to keep the main reactor unblocked."""
        slot_released = False
        try:
            conn.setblocking(True)
            conn.settimeout(15.0)
            res = self.handler(req)
            resp_bytes = serialize_frame(res.to_dict())
            conn.sendall(resp_bytes)
        except Exception as e:
            try:
                err_res = IPCResponse.error(
                    request_id=getattr(req, "request_id", ""),
                    error_msg=f"Error en inferencia o IPC: {e}",
                )
                conn.sendall(serialize_frame(err_res.to_dict()))
            except Exception:
                pass
        finally:
            with self._worker_lock:
                current_thread = threading.current_thread()
                self._worker_threads.discard(current_thread)
                if acquired and not slot_released:
                    try:
                        self._cognitive_semaphore.release()
                        slot_released = True
                    except ValueError:
                        pass
            try:
                conn.close()
            except OSError:
                pass

    def _close_client(self, conn: socket.socket) -> None:
        """Unregister and close client socket."""
        if self._selector:
            try:
                self._selector.unregister(conn)
            except (KeyError, ValueError, OSError):
                pass
        self._buffers.pop(conn, None)
        self._conn_timestamps.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass

    def stop(self, timeout_seconds: float = 2.0) -> bool:
        """Stop server, close listening socket, wait for workers, and report clean/incomplete shutdown."""
        if self._stopped:
            return self._clean_shutdown

        with self._shutdown_lock:
            if self._stopping:
                return self._clean_shutdown
            self._stopping = True

        # 1. Close listening server socket first to stop accepting new connections
        if self._server_sock:
            try:
                if self._selector:
                    try:
                        self._selector.unregister(self._server_sock)
                    except (KeyError, ValueError, OSError):
                        pass
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

        # 2. Close selector
        if self._selector:
            try:
                self._selector.close()
            except Exception:
                pass
            self._selector = None

        # 3. Join active worker threads within deadline
        with self._worker_lock:
            threads = list(self._worker_threads)

        deadline = time.monotonic() + max(0.05, timeout_seconds)
        for t in threads:
            remaining = max(0.01, deadline - time.monotonic())
            if t.is_alive():
                t.join(timeout=remaining)

        # 4. Check residual threads and record shutdown status
        with self._worker_lock:
            residual = [t for t in self._worker_threads if t.is_alive()]
            self._residual_workers = residual
            self._clean_shutdown = (len(residual) == 0)

        # 5. Clean up any remaining client connections in buffer
        for conn in list(self._buffers.keys()):
            self._close_client(conn)
        self._buffers.clear()
        self._conn_timestamps.clear()

        # 6. Unlink socket file safely
        if os.path.lexists(self.socket_path):
            try:
                st = os.lstat(self.socket_path)
                current_uid = os.getuid() if hasattr(os, "getuid") else 1000
                if stat.S_ISSOCK(st.st_mode) and st.st_uid == current_uid:
                    self.socket_path.unlink()
            except OSError:
                pass

        self._stopped = True
        return self._clean_shutdown
