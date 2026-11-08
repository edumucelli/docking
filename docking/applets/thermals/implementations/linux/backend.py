from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from docking.applets.thermals.backend import ThermalsBackend
from docking.applets.thermals.state import (
    HWMON_DIR,
    THERMAL_DIR,
    ThermalSnapshot,
)

"""Linux thermal readings from lm-sensors and sysfs."""

from .readers import (
    FallbackThermalsBackend,
    SensorsThermalsBackend,
    SysfsThermalsBackend,
)


class LinuxThermalsBackend(ThermalsBackend):
    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        hwmon_dir: Path = HWMON_DIR,
        thermal_dir: Path = THERMAL_DIR,
    ) -> None:
        self._reader = FallbackThermalsBackend(
            primary=SensorsThermalsBackend(which=which, run=run),
            fallback=SysfsThermalsBackend(hwmon_dir=hwmon_dir, thermal_dir=thermal_dir),
        )

    def get_snapshot(self) -> ThermalSnapshot:
        return self._reader.get_snapshot()
