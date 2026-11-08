from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ...gpu import (
    AMD_VENDOR_ID,
    AMD_VENDOR_NAME,
    DRM_DIR,
    GpuStats,
    _bytes_to_mib,
    _int_or_none,
    _percent_to_fraction,
)
from ..shared.gpu import VendorGpuReader


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


class GpuReader(VendorGpuReader):
    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        drm_dir: Path = DRM_DIR,
    ) -> None:
        super().__init__(which=which, run=run)
        self._drm_dir = drm_dir

    def read(self) -> GpuStats | None:
        return self._read_nvidia_smi() or self._read_amdgpu_sysfs()

    def _read_amdgpu_sysfs(self) -> GpuStats | None:
        for card_dir in sorted(self._drm_dir.glob("card*")):
            device_dir = card_dir / "device"
            if not device_dir.is_dir():
                continue
            if _read_text(device_dir / "vendor") != AMD_VENDOR_ID:
                continue
            utilization = _percent_to_fraction(
                _read_text(device_dir / "gpu_busy_percent") or ""
            )
            memory_used = _bytes_to_mib(
                _int_or_none(_read_text(device_dir / "mem_info_vram_used") or "")
            )
            memory_total = _bytes_to_mib(
                _int_or_none(_read_text(device_dir / "mem_info_vram_total") or "")
            )
            if utilization is None and memory_used is None and memory_total is None:
                continue
            return GpuStats(
                name=_read_text(device_dir / "product_name") or AMD_VENDOR_NAME,
                utilization=utilization,
                memory_used_mib=memory_used,
                memory_total_mib=memory_total,
            )
        return None
