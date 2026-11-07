"""BSD temperature selection shared by thermal applets."""

from docking.applets.thermals.backend import ThermalsBackend
from docking.applets.thermals.state import ThermalReading, ThermalSnapshot
from docking.platform.environment import is_netbsd, is_openbsd


def temperatures() -> list[tuple[str, float]]:
    if is_netbsd():
        from .netbsd import temperatures as read_temperatures
    elif is_openbsd():
        from .openbsd import temperatures as read_temperatures
    else:
        from .freebsd import temperatures as read_temperatures

    return read_temperatures()


class BsdThermalsBackend(ThermalsBackend):
    def get_snapshot(self) -> ThermalSnapshot:
        readings = temperatures()
        hottest = max(readings, key=lambda reading: reading[1]) if readings else None
        return ThermalSnapshot(
            available=True,
            hottest=ThermalReading("", hottest[0], hottest[1]) if hottest else None,
        )
