"""OpenBSD temperature readings from the kernel sensor tree."""

import re

from docking.platform.commands import read_command as command


def temperatures() -> list[tuple[str, float]]:
    output = command(["sysctl", "hw.sensors"]) or ""
    return [
        (name, float(value))
        for name, value in re.findall(
            r"(hw\.sensors\.[^=\s]+)=\s*(-?\d+(?:\.\d+)?) degC", output
        )
    ]
