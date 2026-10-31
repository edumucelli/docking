from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable

from docking.applets.systemmonitor.gpu import GpuSource

from ...gpu import (
    NVIDIA_SMI_BIN,
    NVIDIA_SMI_TIMEOUT_S,
    GpuStats,
    log,
    parse_nvidia_smi_output,
)


class VendorGpuReader(GpuSource):
    """Read a minimal GPU snapshot from available local tools."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._which = which
        self._run = run
        self._nvidia_available: bool | None = None

    def read(self) -> GpuStats | None:
        """Return current GPU stats, or ``None`` when unavailable."""
        return self._read_nvidia_smi()

    def _read_nvidia_smi(self) -> GpuStats | None:
        if self._nvidia_available is False:
            return None
        if self._nvidia_available is None:
            self._nvidia_available = self._which(NVIDIA_SMI_BIN) is not None
        if not self._nvidia_available:
            return None

        try:
            result = self._run(
                [
                    NVIDIA_SMI_BIN,
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=NVIDIA_SMI_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("Failed to run nvidia-smi: %s", exc)
            return None
        if result.returncode != 0:
            log.debug("nvidia-smi returned %s: %s", result.returncode, result.stderr)
            return None
        return parse_nvidia_smi_output(result.stdout)
