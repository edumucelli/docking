"""State and command helpers for screenshot applet."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple


class Tool(NamedTuple):
    """A screenshot backend with per-mode command templates."""

    command: str
    full: list[str]
    window: list[str]
    region: list[str]


Mode = Literal["full", "window", "region"]


_TOOLS: tuple[Tool, ...] = (
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


def _detect_tool() -> Tool | None:
    """Return the first available screenshot tool, or None."""
    for tool in _TOOLS:
        if shutil.which(tool.command):
            return tool
    return None


def _scrot_path() -> str:
    """Generate a timestamped output path for scrot."""
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return str(Path.home() / "Pictures" / f"Screenshot_{ts}.png")


def _mode_args(*, tool: Tool, mode: Mode) -> list[str]:
    if mode == "full":
        return tool.full
    if mode == "window":
        return tool.window
    return tool.region


def _run(tool: Tool, mode: Mode) -> list[str]:
    """Build and run screenshot command for *tool* and *mode*."""
    args = _mode_args(tool=tool, mode=mode)
    cmd = [tool.command, *args]
    if tool.command == "scrot":
        cmd.append(_scrot_path())
    subprocess.Popen(cmd, start_new_session=True)
    return cmd
