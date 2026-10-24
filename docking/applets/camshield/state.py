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
    locked: bool = False


def probe_camera_state(
    *,
    dev_root: Path | str = "/dev",
    proc_root: Path | str = "/proc",
) -> CamshieldState:
    from docking.applets.camshield.backend import create_backend

    return create_backend().probe(dev_root=Path(dev_root), proc_root=Path(proc_root))


def build_tooltip(state: CamshieldState) -> str:
    """Build a compact user-facing tooltip."""
    lines = [_("Cam Shield")]
    if not state.available:
        lines.append(_("No camera devices found"))
        return "\n".join(lines)

    if state.locked:
        lines.append(_("Camera locked"))

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


def _normalized_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()
