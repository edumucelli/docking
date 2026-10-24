"""Linux thermal readings from lm-sensors and sysfs."""

from docking.applets.thermals.state import (
    FallbackThermalsBackend,
    SensorsThermalsBackend,
    SysfsThermalsBackend,
    ThermalsBackend,
    ThermalSnapshot,
)


class LinuxThermalsBackend(ThermalsBackend):
    def __init__(self, *, which, run, hwmon_dir, thermal_dir) -> None:
        self._reader = FallbackThermalsBackend(
            primary=SensorsThermalsBackend(which=which, run=run),
            fallback=SysfsThermalsBackend(hwmon_dir=hwmon_dir, thermal_dir=thermal_dir),
        )

    def get_snapshot(self) -> ThermalSnapshot:
        return self._reader.get_snapshot()
