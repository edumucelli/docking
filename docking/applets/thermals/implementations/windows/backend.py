"""The current Windows port does not provide native thermal acquisition."""

from docking.applets.thermals.backend import ThermalsBackend
from docking.applets.thermals.state import ThermalSnapshot


class WindowsThermalsBackend(ThermalsBackend):
    def get_snapshot(self) -> ThermalSnapshot:
        return ThermalSnapshot(available=False)
