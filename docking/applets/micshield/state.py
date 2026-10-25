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

"""Microphone values and presentation shared by platform backends."""

from __future__ import annotations

from dataclasses import dataclass

from docking.i18n import _

DEFAULT_POLL_INTERVAL_S = 2
MAX_TOOLTIP_STREAMS = 6


@dataclass(frozen=True, slots=True)
class MicStream:
    """One active microphone capture stream reported by the audio server."""

    stream_id: int | str
    command: str
    pid: int | None = None
    name: str = ""


@dataclass(frozen=True, slots=True)
class MicShieldState:
    """Current microphone privacy state."""

    available: bool
    muted: bool | None
    active: bool | None
    streams: tuple[MicStream, ...] = ()
    error: bool = False


def build_tooltip(state: MicShieldState) -> str:
    """Build compact tooltip text."""
    lines = [_("Mic Shield")]
    if not state.available:
        lines.append(
            _("Microphone state unavailable")
            if state.error
            else _("No microphone source found")
        )
        return "\n".join(lines)

    lines.append(mute_status(state))
    if state.active is None:
        lines.append(_("Microphone activity unknown"))
        return "\n".join(lines)
    if not state.active:
        lines.append(_("Microphone idle"))
        return "\n".join(lines)

    lines.append(_("Microphone active"))
    for stream in state.streams[:MAX_TOOLTIP_STREAMS]:
        lines.append(stream_label(stream))
    remaining = len(state.streams) - MAX_TOOLTIP_STREAMS
    if remaining > 0:
        lines.append(_("{count} more").format(count=remaining))
    return "\n".join(lines)


def stream_label(stream: MicStream) -> str:
    """Menu label for one active capture stream."""
    if stream.pid is None:
        return stream.command
    return _("{command} (PID {pid})").format(
        command=stream.command,
        pid=stream.pid,
    )


def mute_status(state: MicShieldState) -> str:
    if state.muted is None:
        return _("Microphone mute state unknown")
    return _("Microphone muted") if state.muted else _("Microphone unmuted")
