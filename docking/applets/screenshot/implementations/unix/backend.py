"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from docking.applets.screenshot.backend import (
    ScreenshotOperations as ScreenshotOperationsBase,
)
from docking.platform.environment import flatpak, is_flatpak, is_wayland_session

from ...state import (
    _PORTAL_DEST,
    _PORTAL_INTERFACE,
    _PORTAL_METHOD,
    _PORTAL_PATH,
    _PORTAL_TOOL,
    _TOOLS,
    Mode,
    Tool,
)


def _portal_available() -> bool:
    """True when the XDG screenshot portal interface is available via gdbus."""
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return False
    try:
        result = subprocess.run(
            [
                gdbus,
                "introspect",
                "--session",
                "--dest",
                _PORTAL_DEST,
                "--object-path",
                _PORTAL_PATH,
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        return (
            result.returncode == 0 and f"interface {_PORTAL_INTERFACE}" in result.stdout
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def _detect_flatpak_host_tool() -> Tool | None:
    flatpak_spawn = flatpak.spawn_path()
    if flatpak_spawn is None:
        return None
    for tool in _TOOLS:
        if flatpak.host_command_available(tool.command):
            return Tool(
                command=tool.command,
                full=tool.full,
                window=tool.window,
                region=tool.region,
                backend="flatpak-host",
            )
    return None


def _detect_tool() -> Tool | None:
    """Return the first available screenshot tool, or None."""
    if is_wayland_session() and _portal_available():
        return _PORTAL_TOOL
    for tool in _TOOLS:
        if shutil.which(tool.command):
            return tool
    if is_flatpak():
        flatpak_host_tool = _detect_flatpak_host_tool()
        if flatpak_host_tool is not None:
            return flatpak_host_tool
    if _portal_available():
        return _PORTAL_TOOL
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


def _delay_args(*, tool: Tool, delay_seconds: int) -> list[str]:
    """Return per-tool delay args."""
    if delay_seconds <= 0:
        return []
    delay = str(delay_seconds)
    if tool.command in {"mate-screenshot", "gnome-screenshot"}:
        return ["-d", delay]
    if tool.command == "xfce4-screenshooter":
        return ["-d", delay]
    if tool.command == "spectacle":
        return ["--delay", delay]
    if tool.command == "flameshot":
        # Flameshot uses milliseconds; all other tools use seconds.
        return ["--delay", str(delay_seconds * 1000)]
    if tool.command == "scrot":
        return ["-d", delay]
    return []


def _portal_args(*, mode: Mode) -> list[str]:
    interactive = "true" if mode != "full" else "false"
    options = f"{{'modal': <true>, 'interactive': <{interactive}>}}"
    return [
        "call",
        "--session",
        "--dest",
        _PORTAL_DEST,
        "--object-path",
        _PORTAL_PATH,
        "--method",
        _PORTAL_METHOD,
        "",
        options,
    ]


def _launch(*, cmd: list[str], delay_seconds: int) -> None:
    """Launch *cmd* immediately or after a simple in-process delay."""
    kwargs: dict[str, Any] = {"start_new_session": True}
    # Suppress stdout/stderr for portal calls - the D-Bus request handle
    # emitted to stdout is noise to the user and the portal completes
    # asynchronously.
    if cmd[0] == "gdbus":
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    if delay_seconds <= 0:
        subprocess.Popen(cmd, **kwargs)
        return
    timer = threading.Timer(
        delay_seconds,
        subprocess.Popen,
        args=(cmd,),
        kwargs=kwargs,
    )
    timer.daemon = True
    timer.start()


def _run(tool: Tool, mode: Mode, delay_seconds: int = 0) -> list[str]:
    """Build and run screenshot command for *tool* and *mode*."""
    if tool.backend == "portal":
        cmd = [tool.command, *_portal_args(mode=mode)]
        _launch(cmd=cmd, delay_seconds=delay_seconds)
    else:
        args = _mode_args(tool=tool, mode=mode)
        command = [tool.command]
        if tool.backend == "flatpak-host":
            command = flatpak.host_command(
                [tool.command],
                require_flatpak=False,
            ) or [tool.command]
        cmd = [*command, *args, *_delay_args(tool=tool, delay_seconds=delay_seconds)]
        if tool.command == "scrot":
            cmd.append(_scrot_path())
        subprocess.Popen(cmd, start_new_session=True)
    return cmd


class ScreenshotOperations(ScreenshotOperationsBase):
    def _detect_tool(self) -> Tool | None:
        return _detect_tool()

    def _run(self, tool: Tool, mode: Mode, delay_seconds: int = 0) -> list[str]:
        return _run(tool=tool, mode=mode, delay_seconds=delay_seconds)
