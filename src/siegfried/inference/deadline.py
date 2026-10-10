"""Monotonic timeout budgets shared by inference startup and HTTP body reads."""

import time
from typing import Any, Optional

from siegfried.core.errors import InferenceDeadlineExceededError


def remaining_timeout(timeout: float, deadline: Optional[float]) -> float:
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise InferenceDeadlineExceededError("Monotonic inference deadline exceeded")
    return min(timeout, remaining)


def read_deadline_chunk(response: Any, size: int, deadline: Optional[float]) -> bytes:
    """Bound each socket read and return after at most one buffered raw read."""
    if deadline is not None:
        remaining = remaining_timeout(float("inf"), deadline)
        # urllib responses wrap an HTTPResponse with a buffered SocketIO stream.
        raw = getattr(getattr(response, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            current = sock.gettimeout()
            sock.settimeout(min(current, remaining) if current is not None else remaining)
    # read() can perform many receives to fill size; read1() returns after one.
    reader = getattr(response, "read1", response.read)
    chunk = reader(size)
    if deadline is not None:
        remaining_timeout(float("inf"), deadline)
    return chunk
