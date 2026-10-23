"""Native BSD process and system readings, isolated from Linux integrations.

psutil uses the native kernel interfaces for process, CPU, memory and mount
queries. Commands are reserved for APIs absent from psutil, notably OpenBSD's
power and sensor interfaces. Nothing here requires a Linux compatibility layer.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

from docking.log import get_logger

log = get_logger("bsd")


def is_bsd() -> bool:
    return sys.platform.startswith(("freebsd", "openbsd"))


def _psutil() -> Any:
    return importlib.import_module("psutil")


def command(argv: list[str]) -> str | None:
    """Read a bounded native command in a predictable locale."""
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=0.5,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Native command unavailable: %s: %s", argv[0], exc)
        return None


def process_executable(pid: int) -> Path | None:
    try:
        process = _psutil().Process(pid)
        if sys.platform.startswith("openbsd"):
            # OpenBSD cannot query a kernel executable path. psutil.exe() guesses
            # via the observer's PATH, which can misidentify another executable.
            argv = process.cmdline()
            value = str(argv[0]) if argv else ""
        else:
            value = str(process.exe())
        path = Path(value)
        return (
            path.resolve() if value and path.is_absolute() and path.is_file() else None
        )
    except Exception as exc:
        # Processes can exit or deny access between enumeration and this read.
        log.debug("Process executable unavailable for %d: %s", pid, exc)
        return None


def process_script(pid: int) -> str | None:
    try:
        argv = _psutil().Process(pid).cmdline()
        if len(argv) < 2 or not re.fullmatch(
            r"python(?:\d+(?:\.\d+)*)?", Path(str(argv[0])).name
        ):
            return None
        script = str(argv[1])
        return (
            Path(script).name
            if not script.startswith("-") and script.endswith(".py")
            else None
        )
    except Exception as exc:
        log.debug("Process command unavailable for %d: %s", pid, exc)
        return None


def cpu_sample() -> tuple[int, int] | None:
    """Return cumulative total/idle milliseconds; never block to sample usage."""
    try:
        times = _psutil().cpu_times()
        return round(sum(float(t) for t in times) * 1000), round(
            float(times.idle) * 1000
        )
    except Exception as exc:
        log.debug("CPU counters unavailable: %s", exc)
        return None


def memory_fraction() -> float | None:
    try:
        return max(0.0, min(1.0, float(_psutil().virtual_memory().percent) / 100))
    except Exception as exc:
        log.debug("Memory counters unavailable: %s", exc)
        return None


def mount_points() -> tuple[Path, ...]:
    try:
        return tuple(
            dict.fromkeys(
                Path(str(p.mountpoint)) for p in _psutil().disk_partitions(all=True)
            )
        )
    except Exception as exc:
        log.debug("Mount table unavailable: %s", exc)
        return ()


class NetworkInterface(NamedTuple):
    name: str
    address: str
    up: bool
    received: int
    sent: int


def network_interfaces() -> list[NetworkInterface]:
    try:
        util = _psutil()
        counters = util.net_io_counters(pernic=True)
        addresses = util.net_if_addrs()
        result = []
        for name, status in util.net_if_stats().items():
            if str(name).startswith("lo") or name not in counters:
                continue
            address = next(
                (
                    str(a.address)
                    for a in addresses.get(name, ())
                    if a.family == socket.AF_INET
                ),
                "",
            )
            counter = counters[name]
            result.append(
                NetworkInterface(
                    str(name),
                    address,
                    bool(status.isup),
                    int(counter.bytes_recv),
                    int(counter.bytes_sent),
                )
            )
        return sorted(
            result,
            key=lambda interface: (
                not interface.up,
                not interface.address,
                interface.name,
            ),
        )
    except Exception as exc:
        log.debug("Network interfaces unavailable: %s", exc)
        return []


_PSEUDO_FS_TYPES = {
    "devfs",
    "fdescfs",
    "procfs",
    "linprocfs",
    "linsysfs",
    "tmpfs",
    "nullfs",
    "mfs",
    "kernfs",
}


def disk_usage() -> list[tuple[str, float]]:
    readings = []
    try:
        util = _psutil()
        partitions = util.disk_partitions(all=True)
    except Exception as exc:
        log.debug("Mount table unavailable: %s", exc)
        return readings
    seen: set[str] = set()
    for partition in partitions:
        path = str(partition.mountpoint)
        if str(partition.fstype) in _PSEUDO_FS_TYPES or path in seen:
            continue
        seen.add(path)
        try:
            readings.append(
                (str(path), float(util.disk_usage(str(path)).percent) / 100)
            )
        except (OSError, ValueError) as exc:
            log.debug("Disk usage unavailable for %s: %s", path, exc)
    return readings


def session_command(original: list[str]) -> list[str] | None:
    """Translate session actions; leave privilege decisions to the native OS."""
    if original[:2] == ["loginctl", "terminate-session"]:
        for argv in (
            ("xfce4-session-logout", "--logout"),
            ("mate-session-save", "--logout-dialog"),
            ("gnome-session-quit", "--logout"),
        ):
            if shutil.which(argv[0]):
                return list(argv)
        return None
    openbsd = sys.platform.startswith("openbsd")
    commands = {
        ("systemctl", "suspend"): ["zzz"] if openbsd else ["acpiconf", "-s", "3"],
        ("systemctl", "reboot"): ["shutdown", "-r", "now"],
        ("systemctl", "poweroff"): ["shutdown", "-hp" if openbsd else "-p", "now"],
    }
    result = commands.get(tuple(original[:2]))
    return result if result and shutil.which(result[0]) else None


class Battery(NamedTuple):
    capacity: int
    status: str
    seconds_remaining: int | None


def battery() -> Battery | None:
    if sys.platform.startswith("openbsd"):
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
            state = "Charging" if status == "3" else "Discharging"
            if ac == "1" and capacity == 100:
                state = "Full"
            remaining = None
            if minutes not in {None, "unknown"}:
                parsed_minutes = int(minutes)
                if parsed_minutes >= 0:
                    remaining = parsed_minutes * 60
            return Battery(capacity, state, remaining)
        except ValueError:
            return None
    try:
        reading = _psutil().sensors_battery()
        if reading is None:
            return None
        capacity = max(0, min(100, round(float(reading.percent))))
        state = "Discharging"
        if reading.power_plugged:
            state = "Full" if capacity == 100 else "Charging"
        seconds = int(reading.secsleft)
        return Battery(capacity, state, seconds if seconds >= 0 else None)
    except Exception as exc:
        log.debug("Battery unavailable: %s", exc)
        return None


def temperatures() -> list[tuple[str, float]]:
    if sys.platform.startswith("openbsd"):
        output = command(["sysctl", "hw.sensors"]) or ""
        return [
            (name, float(value))
            for name, value in re.findall(
                r"(hw\.sensors\.[^=\s]+)=\s*(-?\d+(?:\.\d+)?) degC", output
            )
        ]
    # psutil added FreeBSD temperature readings after its initial BSD support.
    try:
        reader = getattr(_psutil(), "sensors_temperatures", None)
        if reader is not None:
            return [
                (str(reading.label or chip), float(reading.current))
                for chip, entries in reader().items()
                for reading in entries
            ]
    except Exception as exc:
        log.debug("Native temperature query unavailable: %s", exc)
    value = command(["sysctl", "-n", "dev.cpu.0.temperature"])
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)C", value or "")
    return [("CPU", float(match[1]))] if match else []
