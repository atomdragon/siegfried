"""System resource detection and budget enforcement for local inference.

RULES:
1. Python Standard Library only.
2. Non-destructive detection (/proc/meminfo, /sys/class/drm).
3. Clear distinction between dedicated VRAM and shared system RAM.
4. If VRAM cannot be measured with certainty, mark as UNKNOWN (None).
5. 3.0 GiB GPU budget is a strict ceiling, not a guaranteed allocation.
6. CPU-only mode is fully supported.
"""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional

from siegfried.core.errors import InsufficientResourcesError


DEFAULT_MAX_GPU_BUDGET_BYTES: int = 3 * 1024 * 1024 * 1024  # 3.0 GiB
DEFAULT_MIN_OS_RAM_RESERVATION_BYTES: int = 2 * 1024 * 1024 * 1024  # 2.0 GiB
DEFAULT_IDLE_TIMEOUT_SECONDS: float = 900.0  # 15 minutes
DEFAULT_MAX_CONCURRENCY: int = 1


@dataclass(frozen=True)
class SystemResources:
    """Snapshot of detected system hardware resources."""
    total_ram_bytes: int
    available_ram_bytes: int
    gpu_detected: bool = False
    gpu_name: Optional[str] = None
    dedicated_vram_bytes: Optional[int] = None
    is_integrated_gpu: bool = False
    cpu_count: int = 1

    @property
    def has_dedicated_vram(self) -> bool:
        return bool(
            self.gpu_detected
            and not self.is_integrated_gpu
            and self.dedicated_vram_bytes is not None
            and self.dedicated_vram_bytes > 0
        )


class ResourceDetector:
    """Non-destructive hardware and memory resource inspector."""

    @staticmethod
    def get_system_resources(
        meminfo_path: Path = Path("/proc/meminfo"),
        drm_path: Path = Path("/sys/class/drm"),
    ) -> SystemResources:
        """Inspect host memory and graphics capabilities."""
        total_ram = 0
        available_ram = 0
        cpu_count = os.cpu_count() or 1

        # 1. Parse RAM from /proc/meminfo
        if meminfo_path.exists():
            try:
                with open(meminfo_path, "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            key = parts[0].strip()
                            val_str = parts[1].strip().split()[0]
                            if val_str.isdigit():
                                kb_val = int(val_str) * 1024
                                if key == "MemTotal":
                                    total_ram = kb_val
                                elif key == "MemAvailable":
                                    available_ram = kb_val
            except Exception:
                pass

        # Fallback to sysconf if meminfo not available
        if total_ram == 0:
            try:
                pages = os.sysconf("SC_PHYS_PAGES")
                page_size = os.sysconf("SC_PAGE_SIZE")
                total_ram = pages * page_size
                available_ram = total_ram // 2
            except Exception:
                total_ram = 8 * 1024 * 1024 * 1024
                available_ram = 4 * 1024 * 1024 * 1024

        # 2. Inspect GPU via /sys/class/drm
        gpu_detected = False
        gpu_name: Optional[str] = None
        is_integrated = False
        dedicated_vram: Optional[int] = None

        if drm_path.exists() and drm_path.is_dir():
            try:
                for entry in drm_path.iterdir():
                    if entry.name.startswith("card") and "-" not in entry.name:
                        gpu_detected = True
                        device_path = entry / "device"
                        if device_path.exists():
                            # Check vendor/device if available
                            vendor_file = device_path / "vendor"
                            if vendor_file.exists():
                                try:
                                    vendor_id = vendor_file.read_text().strip().lower()
                                    # Intel vendor ID 0x8086 typically integrated on laptops
                                    if vendor_id == "0x8086":
                                        is_integrated = True
                                        gpu_name = "Intel Integrated Graphics"
                                    elif vendor_id == "0x10de":
                                        is_integrated = False
                                        gpu_name = "NVIDIA Dedicated GPU"
                                    elif vendor_id == "0x1002":
                                        is_integrated = False
                                        gpu_name = "AMD GPU"
                                except Exception:
                                    pass
            except Exception:
                pass

        return SystemResources(
            total_ram_bytes=total_ram,
            available_ram_bytes=available_ram,
            gpu_detected=gpu_detected,
            gpu_name=gpu_name,
            dedicated_vram_bytes=dedicated_vram,
            is_integrated_gpu=is_integrated,
            cpu_count=cpu_count,
        )


@dataclass(frozen=True)
class ResourceBudget:
    """Enforces resource allocation ceilings and OS safety reservations."""
    max_gpu_budget_bytes: int = DEFAULT_MAX_GPU_BUDGET_BYTES
    min_os_ram_reservation_bytes: int = DEFAULT_MIN_OS_RAM_RESERVATION_BYTES
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    force_cpu_only: bool = False

    def validate_memory_headroom(
        self,
        estimated_model_bytes: int,
        resources: SystemResources,
    ) -> None:
        """Verify that loading the model will not deplete mandatory OS RAM reservations.

        Raises:
            InsufficientResourcesError: If available RAM is below the required threshold.
        """
        required_ram = estimated_model_bytes + self.min_os_ram_reservation_bytes
        if resources.available_ram_bytes < required_ram:
            avail_mb = resources.available_ram_bytes / (1024 * 1024)
            req_mb = required_ram / (1024 * 1024)
            raise InsufficientResourcesError(
                f"Insufficient system RAM: available {avail_mb:.1f} MiB, "
                f"required {req_mb:.1f} MiB (model + {self.min_os_ram_reservation_bytes // (1024*1024)} MiB OS safety reservation)"
            )

    def calculate_gpu_layers(
        self,
        total_layers: int,
        estimated_bytes_per_layer: int,
        resources: SystemResources,
    ) -> int:
        """Determine safe number of offloaded GPU layers respecting the 3.0 GiB ceiling."""
        if self.force_cpu_only or not resources.gpu_detected or resources.is_integrated_gpu:
            return 0

        if estimated_bytes_per_layer <= 0 or total_layers <= 0:
            return 0

        # Offload layers within 3.0 GiB ceiling
        max_layers_by_ceiling = self.max_gpu_budget_bytes // estimated_bytes_per_layer

        if resources.has_dedicated_vram and resources.dedicated_vram_bytes is not None:
            max_layers_by_vram = resources.dedicated_vram_bytes // estimated_bytes_per_layer
            allowed_layers = min(total_layers, max_layers_by_ceiling, max_layers_by_vram)
        else:
            # If dedicated VRAM is UNKNOWN, cap strictly by configured ceiling
            allowed_layers = min(total_layers, max_layers_by_ceiling)

        return max(0, int(allowed_layers))
