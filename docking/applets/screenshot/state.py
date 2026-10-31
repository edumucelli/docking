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

"""State and command helpers for screenshot applet."""

from __future__ import annotations

from typing import Literal, NamedTuple


class Tool(NamedTuple):
    """A screenshot backend with per-mode command templates."""

    command: str
    full: list[str]
    window: list[str]
    region: list[str]
    backend: str = "cli"


Mode = Literal["full", "window", "region"]

_PORTAL_TOOL = Tool(
    command="gdbus",
    full=[],
    window=[],
    region=[],
    backend="portal",
)
_PORTAL_DEST = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_PORTAL_INTERFACE = "org.freedesktop.portal.Screenshot"
_PORTAL_METHOD = f"{_PORTAL_INTERFACE}.Screenshot"


_TOOLS: tuple[Tool, ...] = (
    Tool(
        command="cosmic-screenshot",
        full=["--interactive=false"],
        window=["--interactive=true"],
        region=["--interactive=true"],
    ),
    Tool(command="mate-screenshot", full=[], window=["-w"], region=["-a"]),
    Tool(command="gnome-screenshot", full=[], window=["-w"], region=["-a"]),
    Tool(command="xfce4-screenshooter", full=["-f"], window=["-w"], region=["-r"]),
    Tool(
        command="spectacle",
        full=["--fullscreen"],
        window=["--activewindow"],
        region=["--region"],
    ),
    Tool(command="flameshot", full=["full"], window=["gui"], region=["gui"]),
    Tool(command="scrot", full=[], window=["-u"], region=["-s"]),
)
