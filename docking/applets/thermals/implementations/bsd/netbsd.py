"""NetBSD temperatures from typed envsys sensor readings."""

from docking.platform.host.implementations.bsd.envsys import read_sensors


def temperatures() -> list[tuple[str, float]]:
    # envsys temperatures are integer microkelvins, even when envstat's
    # human-readable display uses Celsius.
    return [
        (f"{sensor.device}: {sensor.name}", sensor.value / 1_000_000 - 273.15)
        for sensor in read_sensors()
        if sensor.unit == "Temperature" and sensor.value >= 0
    ]
