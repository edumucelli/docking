"""Select the thermal reader for the operating system."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from docking.applets.thermals.state import HWMON_DIR, THERMAL_DIR, ThermalsBackend
from docking.platform import environment


def create_backend(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    hwmon_dir: Path = HWMON_DIR,
    thermal_dir: Path = THERMAL_DIR,
) -> ThermalsBackend:
    if environment.is_bsd() and hwmon_dir == HWMON_DIR and thermal_dir == THERMAL_DIR:
        from docking.applets.thermals.bsd import BsdThermalsBackend

        return BsdThermalsBackend()
    from docking.applets.thermals.linux import LinuxThermalsBackend

    return LinuxThermalsBackend(
        which=which, run=run, hwmon_dir=hwmon_dir, thermal_dir=thermal_dir
    )
