"""Pure absence estimates. Neither machine absence nor locking proves sleep."""
from dataclasses import dataclass
from enum import Enum
import math
from typing import Optional


class RestStatus(str, Enum):
    ESTIMATED = "ESTIMATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID_INTERVAL = "INVALID_INTERVAL"


@dataclass(frozen=True)
class ClockSample:
    wall: float
    monotonic: Optional[float] = None
    boottime: Optional[float] = None
    boot_id: Optional[str] = None

    def valid(self) -> bool:
        def number(value, positive=False):
            try:
                return (type(value) in (int, float) and math.isfinite(value)
                        and (value > 0 if positive else value >= 0))
            except (OverflowError, ValueError):
                return False
        return (number(self.wall, True) and
                all(value is None or number(value) for value in (self.monotonic, self.boottime)) and
                (self.boot_id is None or (isinstance(self.boot_id, str) and
                                         0 < len(self.boot_id) <= 64)))


@dataclass(frozen=True)
class RestEstimate:
    status: RestStatus
    elapsed_seconds: Optional[float] = None
    estimated_minutes: Optional[float] = None
    reason: str = ""

    def describe(self) -> str:
        if self.status != RestStatus.ESTIMATED:
            return self.status.value
        minutes = int(self.estimated_minutes)
        return ("Se detectó una ausencia prolongada del equipo. La ventana de descanso "
                f"estimada es de {minutes // 60} h {minutes % 60} min.")


def estimate_rest(start: Optional[ClockSample], end: Optional[ClockSample], *,
                  cause: str, continuous_evidence: bool,
                  wall_clock_verified: bool = False) -> RestEstimate:
    """90 min minimum; subtract 25 min only with a corroborated absence pair.

    Same-boot BOOTTIME includes suspension. Across boots, or without BOOTTIME,
    an explicit independently verified wall interval is required. Merely finding
    two wall timestamps in a log is insufficient. MONOTONIC alone is never used
    to measure suspended time. Clock disagreement >5 seconds is rejected.
    """
    if type(continuous_evidence) is not bool or type(wall_clock_verified) is not bool:
        return RestEstimate(RestStatus.INVALID_INTERVAL, reason="invalid_evidence_flags")
    if start is None or end is None:
        return RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="missing_endpoint")
    if not isinstance(start, ClockSample) or not isinstance(end, ClockSample) or not start.valid() or not end.valid():
        return RestEstimate(RestStatus.INVALID_INTERVAL, reason="invalid_clock_sample")
    wall_elapsed = end.wall - start.wall
    if wall_elapsed < 0:
        return RestEstimate(RestStatus.INVALID_INTERVAL, reason="wall_clock_reversed")
    same_boot = start.boot_id is not None and start.boot_id == end.boot_id
    if same_boot and start.monotonic is not None and end.monotonic is not None:
        if end.monotonic < start.monotonic:
            return RestEstimate(RestStatus.INVALID_INTERVAL, reason="monotonic_reversed")
    if cause not in ("suspend", "shutdown", "explicit_retirement") or not continuous_evidence:
        return RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="absence_not_corroborated")
    if same_boot and start.boottime is not None and end.boottime is not None:
        elapsed = end.boottime - start.boottime
        if elapsed < 0 or abs(elapsed - wall_elapsed) > 5.0:
            return RestEstimate(RestStatus.INVALID_INTERVAL, reason="clocks_disagree")
        if (start.monotonic is not None and end.monotonic is not None and
                end.monotonic - start.monotonic > elapsed + 1.0):
            return RestEstimate(RestStatus.INVALID_INTERVAL, reason="clocks_disagree")
    elif wall_clock_verified:
        elapsed = wall_elapsed
    else:
        return RestEstimate(RestStatus.INSUFFICIENT_DATA, reason="wall_interval_unverified")
    if elapsed < 90 * 60:
        return RestEstimate(RestStatus.NOT_APPLICABLE, elapsed_seconds=elapsed,
                            reason="absence_below_90_minutes")
    return RestEstimate(RestStatus.ESTIMATED, elapsed, elapsed / 60 - 25,
                        "absence_estimate_not_biometric_sleep")
