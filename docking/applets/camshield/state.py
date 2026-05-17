# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Camera device holder detection for Cam Shield."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from docking.i18n import _

DEFAULT_POLL_INTERVAL_S = 2
MAX_TOOLTIP_HOLDERS = 6


@dataclass(frozen=True, slots=True)
class CameraHolder:
    """One process currently holding at least one camera device."""

    pid: int
    command: str
    devices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CamshieldState:
    """Current camera privacy state."""

    available: bool
    active: bool
    devices: tuple[str, ...] = ()
    holders: tuple[CameraHolder, ...] = ()


def probe_camera_state(
    *,
    dev_root: Path | str = "/dev",
    proc_root: Path | str = "/proc",
) -> CamshieldState:
    """Return whether any process currently holds a ``/dev/video*`` device."""
    video_devices = _video_devices(Path(dev_root))
    if not video_devices:
        return CamshieldState(available=False, active=False)

    holders = _camera_holders(
        proc_root=Path(proc_root),
        video_devices=video_devices,
    )
    return CamshieldState(
        available=True,
        active=bool(holders),
        devices=tuple(sorted(video_devices)),
        holders=tuple(sorted(holders, key=lambda holder: (holder.command, holder.pid))),
    )


def build_tooltip(state: CamshieldState) -> str:
    """Build a compact user-facing tooltip."""
    lines = [_("Cam Shield")]
    if not state.available:
        lines.append(_("No camera devices found"))
        return "\n".join(lines)

    if not state.active:
        lines.append(_("Camera idle"))
        return "\n".join(lines)

    lines.append(_("Camera active"))
    for holder in state.holders[:MAX_TOOLTIP_HOLDERS]:
        lines.append(
            _("{command} (PID {pid}) using {devices}").format(
                command=holder.command,
                pid=holder.pid,
                devices=", ".join(holder.devices),
            )
        )
    remaining = len(state.holders) - MAX_TOOLTIP_HOLDERS
    if remaining > 0:
        lines.append(_("{count} more").format(count=remaining))
    return "\n".join(lines)


def holder_label(holder: CameraHolder) -> str:
    """Menu label for one holder process."""
    return _("{command} (PID {pid}) - {devices}").format(
        command=holder.command,
        pid=holder.pid,
        devices=", ".join(holder.devices),
    )


def _video_devices(dev_root: Path) -> dict[str, Path]:
    try:
        entries = dev_root.glob("video*")
        return {
            entry.name: _normalized_path(entry)
            for entry in entries
            if entry.name.startswith("video") and entry.name[5:].isdigit()
        }
    except OSError:
        return {}


def _camera_holders(
    *,
    proc_root: Path,
    video_devices: dict[str, Path],
) -> list[CameraHolder]:
    by_pid: dict[int, set[str]] = defaultdict(set)

    try:
        proc_entries = tuple(proc_root.iterdir())
    except OSError:
        proc_entries = ()

    for proc_entry in proc_entries:
        if not proc_entry.name.isdigit():
            continue
        pid = int(proc_entry.name)
        fd_dir = proc_entry / "fd"
        try:
            fd_entries = tuple(fd_dir.iterdir())
        except OSError:
            continue

        for fd_entry in fd_entries:
            target_name = _fd_video_device_name(fd_entry, video_devices)
            if target_name is not None:
                by_pid[pid].add(target_name)

    holders: list[CameraHolder] = []
    for pid, devices in by_pid.items():
        holders.append(
            CameraHolder(
                pid=pid,
                command=_process_command(proc_root / str(pid)),
                devices=tuple(sorted(devices)),
            )
        )
    return holders


def _fd_video_device_name(
    fd_entry: Path,
    video_devices: dict[str, Path],
) -> str | None:
    try:
        target = str(fd_entry.readlink())
    except OSError:
        return None

    if target.endswith(" (deleted)"):
        target = target[: -len(" (deleted)")]

    target_path = _normalized_path(Path(target))
    for name, device_path in video_devices.items():
        if target_path == device_path:
            return name
    return None


def _normalized_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _process_command(proc_entry: Path) -> str:
    comm = _read_first_line(proc_entry / "comm")
    if comm:
        return comm

    cmdline = _read_text(proc_entry / "cmdline")
    if cmdline:
        first = cmdline.split("\0", 1)[0]
        if first:
            return Path(first).name

    return _("Unknown")


def _read_first_line(path: Path) -> str:
    text = _read_text(path)
    if not text:
        return ""
    return text.splitlines()[0].strip()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
