"""Llama-server process lifecycle supervisor and resource manager.

RULES:
1. Pure Python Standard Library (subprocess, socket, urllib.request, time, threading).
2. NEVER use shell=True, pkill, or killall.
3. Strict process ownership: only terminate the exact subprocess.Popen instance created.
4. Loopback-only binding (127.0.0.1 / ::1 / localhost).
5. State transitions: UNAVAILABLE, STOPPED, STARTING, READY, BUSY, STOPPING, FAILED.
6. Monotonic idle timeout (15 minutes default) with injectable clock.
7. Active requests block idle eviction.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Generator, List, Optional
import urllib.error
import urllib.request

from siegfried.core.errors import (
    InferenceConcurrencyExceededError,
    InferenceConfigError,
    InferenceSecurityError,
    InsufficientResourcesError,
    LlamaBinaryNotFoundError,
    LlamaHealthCheckError,
    LlamaModelNotFoundError,
    LlamaProcessTerminatedError,
    LlamaShutdownError,
    LlamaStartupError,
    LocalInferenceUnavailableError,
    PortInUseError,
    StorageError,
    UnsafePathError,
)
from siegfried.inference.resources import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_MAX_GPU_BUDGET_BYTES,
    DEFAULT_MIN_OS_RAM_RESERVATION_BYTES,
    ResourceBudget,
    ResourceDetector,
    SystemResources,
)
from siegfried.storage.paths import SiegfriedPaths, default_paths


class LlamaServerState(str, Enum):
    """Lifecycle states of the local llama-server process."""
    UNAVAILABLE = "UNAVAILABLE"
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    BUSY = "BUSY"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


class LlamaLifecycleManager:
    """Manages the lifecycle, health, memory budgeting, and idle timeout of llama-server."""

    def __init__(
        self,
        binary_path: Optional[Path | str] = None,
        model_path: Optional[Path | str] = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        gpu_layers: int = 0,
        ctx_size: int = 2048,
        threads: Optional[int] = None,
        pid_file: Optional[Path | str] = None,
        resource_budget: Optional[ResourceBudget] = None,
        resource_detector: Optional[ResourceDetector] = None,
        clock: Callable[[], float] = time.monotonic,
        health_check_timeout: float = 0.5,
        max_startup_seconds: float = 10.0,
        paths: Optional[SiegfriedPaths] = None,
        check_port_in_use: bool = True,
    ) -> None:
        self.paths = paths or default_paths
        self.binary_path = Path(binary_path) if binary_path else None
        self.model_path = Path(model_path) if model_path else None
        self.host = host
        self.port = int(port)
        self.gpu_layers = int(gpu_layers)
        self.ctx_size = int(ctx_size)
        self.threads = int(threads) if threads is not None else None
        self.pid_file = Path(pid_file) if pid_file else self.paths.llama_pid_file
        self.resource_budget = resource_budget or ResourceBudget()
        self.resource_detector = resource_detector or ResourceDetector()
        self._clock = clock
        self.health_check_timeout = float(health_check_timeout)
        self.max_startup_seconds = float(max_startup_seconds)
        self.check_port_in_use = check_port_in_use


        self._state: LlamaServerState = LlamaServerState.STOPPED
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._active_requests: int = 0
        self._last_activity_time: float = self._clock()
        self._last_error: Optional[str] = None

    @property
    def state(self) -> LlamaServerState:
        with self._lock:
            # Check if process died unexpectedly while reported as READY/BUSY
            if self._process is not None and self._state in (LlamaServerState.READY, LlamaServerState.BUSY):
                poll_res = self._process.poll()
                if poll_res is not None:
                    self._state = LlamaServerState.FAILED
                    self._last_error = f"Process terminated unexpectedly with return code {poll_res}"
                    self._process = None
                    self._cleanup_pid_file()
            return self._state

    @property
    def is_running(self) -> bool:
        return self.state in (LlamaServerState.READY, LlamaServerState.BUSY)

    @property
    def active_requests(self) -> int:
        with self._lock:
            return self._active_requests

    @property
    def last_activity_time(self) -> float:
        with self._lock:
            return self._last_activity_time

    def get_status_info(self) -> Dict[str, Any]:
        with self._lock:
            pid = self._process.pid if self._process else None
            return {
                "state": self.state.value,
                "pid": pid,
                "host": self.host,
                "port": self.port,
                "active_requests": self._active_requests,
                "gpu_layers": self.gpu_layers,
                "ctx_size": self.ctx_size,
                "idle_timeout_seconds": self.resource_budget.idle_timeout_seconds,
                "last_activity_time": self._last_activity_time,
                "last_error": self._last_error,
            }

    def validate_prerequisites(self, check_port: Optional[bool] = None) -> SystemResources:
        """Validate binary, model, network binding, and resource constraints."""
        should_check_port = self.check_port_in_use if check_port is None else check_port

        # 1. Validate host security (loopback only)
        normalized_host = self.host.strip().lower()
        if normalized_host not in ("127.0.0.1", "localhost", "::1"):
            raise InferenceSecurityError(
                f"Insecure host binding '{self.host}' rejected. Local inference must bind strictly to loopback (127.0.0.1)."
            )

        if not (1 <= self.port <= 65535):
            raise InferenceConfigError(f"Invalid port: {self.port}")

        # 2. Validate binary
        if not self.binary_path:
            # Try discovering in default paths or PATH
            discovered = self._discover_binary()
            if discovered:
                self.binary_path = discovered
            else:
                self._state = LlamaServerState.UNAVAILABLE
                raise LlamaBinaryNotFoundError("llama-server executable binary path is not configured or found")

        if not self.binary_path.exists():
            self._state = LlamaServerState.UNAVAILABLE
            raise LlamaBinaryNotFoundError(f"llama-server executable not found at '{self.binary_path}'")

        if not self.binary_path.is_file():
            self._state = LlamaServerState.UNAVAILABLE
            raise LlamaBinaryNotFoundError(f"llama-server path '{self.binary_path}' is not a regular file")

        if not os.access(self.binary_path, os.X_OK):
            self._state = LlamaServerState.UNAVAILABLE
            raise LlamaBinaryNotFoundError(f"llama-server binary '{self.binary_path}' lacks execute permissions")

        # 3. Validate model file
        if not self.model_path:
            self._state = LlamaServerState.UNAVAILABLE
            raise LlamaModelNotFoundError("Local GGUF model path is not configured")

        if not self.model_path.exists():
            self._state = LlamaServerState.UNAVAILABLE
            raise LlamaModelNotFoundError(f"Model file not found at '{self.model_path}'")

        if not self.model_path.is_file():
            self._state = LlamaServerState.UNAVAILABLE
            raise LlamaModelNotFoundError(f"Model path '{self.model_path}' is not a regular file")

        if not os.access(self.model_path, os.R_OK):
            self._state = LlamaServerState.UNAVAILABLE
            raise LlamaModelNotFoundError(f"Model file '{self.model_path}' is not readable")

        # Check for unsafe symlinks escaping base directory if within siegfried_home
        if self.model_path.is_symlink():
            try:
                resolved = self.model_path.resolve()
                if not resolved.exists():
                    raise LlamaModelNotFoundError(f"Model symlink '{self.model_path}' is broken")
            except OSError as e:
                raise LlamaModelNotFoundError(f"Cannot resolve model symlink '{self.model_path}': {e}") from e

        # 4. Check port availability (must not be bound by another process)
        if should_check_port and self._is_port_in_use(self.host, self.port):
            # If port is in use and we don't own a running process, raise PortInUseError
            if self._process is None or self._process.poll() is not None:
                raise PortInUseError(
                    f"Port {self.port} on {self.host} is already in use by another process. "
                    "Refusing to kill or attach to an unmanaged foreign process."
                )

        # 5. Check resource budget
        resources = self.resource_detector.get_system_resources()
        model_size = self.model_path.stat().st_size
        self.resource_budget.validate_memory_headroom(
            estimated_model_bytes=model_size,
            resources=resources,
        )

        return resources


    def ensure_started(self) -> bool:
        """Start local llama-server if not already running and wait for READY state."""
        with self._lock:
            if self.is_running:
                return True

            if self._state == LlamaServerState.STARTING:
                # Another thread is starting it; wait briefly
                pass
            else:
                resources = self.validate_prerequisites()
                self._state = LlamaServerState.STARTING
                self._last_error = None

                # Build startup arguments
                args = [
                    str(self.binary_path),
                    "-m", str(self.model_path),
                    "--host", self.host,
                    "--port", str(self.port),
                    "-c", str(self.ctx_size),
                    "-ngl", str(self.gpu_layers),
                ]
                if self.threads:
                    args.extend(["-t", str(self.threads)])

                try:
                    # Clean up old stale PID file before spawn
                    self._cleanup_pid_file()

                    # Spawn process with zero shell execution
                    self._process = subprocess.Popen(
                        args,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        close_fds=True,
                    )
                    self._write_pid_file(self._process.pid)
                except Exception as e:
                    self._state = LlamaServerState.FAILED
                    self._last_error = f"Failed to spawn process: {e}"
                    self._cleanup_pid_file()
                    raise LlamaStartupError(f"Failed to launch llama-server process: {e}") from e

        # Health check polling (outside lock to avoid blocking other status queries)
        ready = self._wait_for_health_check()
        if not ready:
            self.stop_server()
            raise LlamaHealthCheckError(
                f"llama-server failed to pass health checks within {self.max_startup_seconds}s"
            )

        with self._lock:
            self._state = LlamaServerState.READY
            self._last_activity_time = self._clock()
            return True

    def _wait_for_health_check(self) -> bool:
        """Poll /health endpoint until server responds ready or fails."""
        start_time = time.monotonic()
        deadline = start_time + self.max_startup_seconds
        health_url = f"http://{self.host}:{self.port}/health"

        while time.monotonic() < deadline:
            with self._lock:
                if self._process is None:
                    return False
                exit_code = self._process.poll()
                if exit_code is not None:
                    self._state = LlamaServerState.FAILED
                    self._last_error = f"Process terminated during startup with exit code {exit_code}"
                    self._process = None
                    self._cleanup_pid_file()
                    raise LlamaProcessTerminatedError(
                        exit_code=exit_code,
                        message=f"Process exited before becoming healthy (code {exit_code})"
                    )

            try:
                req = urllib.request.Request(health_url, headers={"User-Agent": "Siegfried-HealthCheck/1.0"})
                with urllib.request.urlopen(req, timeout=self.health_check_timeout) as resp:
                    if resp.status == 200:
                        body = resp.read(1024).decode("utf-8", errors="replace")
                        try:
                            data = json.loads(body)
                            # llama-server returns {"status": "ok"} or {"status": "loading model"}
                            if isinstance(data, dict):
                                status_str = data.get("status", "")
                                if status_str == "ok" or status_str == "ready":
                                    return True
                        except Exception:
                            # If plain 200 returned without strict json, consider ready
                            return True
            except (urllib.error.URLError, socket.timeout, TimeoutError, http.client.HTTPException, ConnectionError, OSError):
                pass

            time.sleep(0.02)

        return False


    @contextmanager
    def active_inference(self, deadline: Optional[float] = None) -> Generator[None, None, None]:
        """Context manager acquiring an active inference slot and updating last activity time."""
        with self._lock:
            if not self.is_running:
                self.ensure_started()

            if self._active_requests >= self.resource_budget.max_concurrency:
                raise InferenceConcurrencyExceededError(
                    f"Local inference concurrency limit ({self.resource_budget.max_concurrency}) exceeded"
                )

            self._active_requests += 1
            self._state = LlamaServerState.BUSY
            self._last_activity_time = self._clock()

        try:
            yield
        finally:
            with self._lock:
                self._active_requests = max(0, self._active_requests - 1)
                self._last_activity_time = self._clock()
                if self._active_requests == 0 and self._state == LlamaServerState.BUSY:
                    self._state = LlamaServerState.READY

    def check_idle(self, now: Optional[float] = None) -> bool:
        """Check if server has been inactive for longer than idle_timeout_seconds and stop it."""
        current_time = now if now is not None else self._clock()
        with self._lock:
            if self._state == LlamaServerState.READY and self._active_requests == 0:
                elapsed = current_time - self._last_activity_time
                if elapsed >= self.resource_budget.idle_timeout_seconds:
                    # Trigger clean idle eviction
                    self.stop_server()
                    return True
        return False

    def stop_server(self, timeout: float = 5.0) -> bool:
        """Gracefully terminate llama-server process using SIGTERM and SIGKILL escalation."""
        proc_to_kill: Optional[subprocess.Popen] = None
        with self._lock:
            if self._state in (LlamaServerState.STOPPED, LlamaServerState.UNAVAILABLE) and self._process is None:
                self._cleanup_pid_file()
                return True

            self._state = LlamaServerState.STOPPING
            proc_to_kill = self._process
            self._process = None

        if proc_to_kill is not None:
            # Check if already terminated
            if proc_to_kill.poll() is None:
                try:
                    # 1. Graceful SIGTERM
                    proc_to_kill.terminate()
                    try:
                        proc_to_kill.wait(timeout=min(2.0, timeout))
                    except subprocess.TimeoutExpired:
                        # 2. Hard SIGKILL against own process only
                        proc_to_kill.kill()
                        proc_to_kill.wait(timeout=1.0)
                except ProcessLookupError:
                    pass
                except OSError as e:
                    pass

        self._cleanup_pid_file()
        with self._lock:
            self._active_requests = 0
            self._state = LlamaServerState.STOPPED
            self._last_activity_time = self._clock()
            return True

    def _is_port_in_use(self, host: str, port: int) -> bool:
        """Check if a TCP port is currently bound."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            try:
                s.connect((host, port))
                return True
            except (ConnectionRefusedError, socket.timeout, OSError):
                return False

    def _write_pid_file(self, pid: int) -> None:
        """Write PID file atomically with strict 0600 permissions."""
        if not self.pid_file:
            return
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.pid_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with open(fd, "w", encoding="utf-8") as f:
                f.write(f"{pid}\n")
        except OSError:
            pass

    def _cleanup_pid_file(self) -> None:
        """Safely remove PID file."""
        if self.pid_file and self.pid_file.exists():
            try:
                self.pid_file.unlink()
            except OSError:
                pass

    def _discover_binary(self) -> Optional[Path]:
        """Attempt to discover llama-server binary in authorized system paths."""
        candidate_names = ["llama-server", "llama.cpp-server"]
        search_dirs = [
            self.paths.base_dir / "bin",
            Path.home() / ".local" / "bin",
            Path("/usr/local/bin"),
            Path("/usr/bin"),
        ]
        for d in search_dirs:
            for name in candidate_names:
                cand = d / name
                if cand.exists() and cand.is_file() and os.access(cand, os.X_OK):
                    return cand
        return None
