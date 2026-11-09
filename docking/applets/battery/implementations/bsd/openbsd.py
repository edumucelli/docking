"""OpenBSD battery readings from apm."""

from __future__ import annotations

from docking.platform.commands import read_command as command

from .backend import Battery


def battery() -> Battery | None:
    percent = command(["apm", "-l"])
    status = command(["apm", "-b"])
    minutes = command(["apm", "-m"])
    ac = command(["apm", "-a"])
    try:
        if status not in {"0", "1", "2", "3"} or percent is None:
            return None
        capacity = int(percent)
        if not 0 <= capacity <= 100:
            return None
        if status == "3":
            state = "Charging"
        elif ac == "1":
            state = "Full"
        else:
            state = "Discharging"
        remaining = None
        if minutes not in {None, "unknown"}:
            parsed_minutes = int(minutes)
            if parsed_minutes >= 0:
                remaining = parsed_minutes * 60
        return Battery(capacity, state, remaining)
    except ValueError:
        return None
