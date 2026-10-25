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
    active: bool | None
    devices: tuple[str, ...] = ()
    holders: tuple[CameraHolder, ...] = ()
    locked: bool = False
    error: bool = False


def build_tooltip(state: CamshieldState) -> str:
    """Build a compact user-facing tooltip."""
    lines = [_("Cam Shield")]
    if not state.available:
        lines.append(
            _("Camera state unavailable")
            if state.error
            else _("No camera devices found")
        )
        return "\n".join(lines)

    if state.locked:
        lines.append(_("Camera locked"))

    if state.active is None:
        lines.append(_("Camera activity unknown"))
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
