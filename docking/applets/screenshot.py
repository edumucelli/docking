"""Screenshot applet -- full screen, window, or region capture."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="screenshot")


class Tool(NamedTuple):
    """A screenshot backend with per-mode command templates."""

    command: str
    full: list[str]
    window: list[str]
    region: list[str]


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


def _run(tool: Tool, mode: str) -> None:
    """Take a screenshot using *tool* in the given *mode*."""
    args: list[str] = getattr(tool, mode)
    cmd = [tool.command, *args]
    if tool.command == "scrot":
        cmd.append(_scrot_path())
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        _log.warning("Failed to run %s: %s", cmd, exc)


class ScreenshotApplet(Applet):
    """Capture screenshots via the best available tool.

    Left-click takes a full-screen capture. Right-click menu offers
    full screen, active window, and region selection modes.
    Auto-detects mate-screenshot, gnome-screenshot, or scrot.
    """

    id = "screenshot"
    name = "Screenshot"
    icon_name = "applets-screenshooter"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._tool = _detect_tool()
        if not self._tool:
            _log.warning(
                "No screenshot tool found (%s)",
                ", ".join(t.command for t in _TOOLS),
            )
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return load_theme_icon(name="applets-screenshooter", size=size)

    def on_clicked(self) -> None:
        """Full-screen capture on left-click."""
        if self._tool:
            _run(tool=self._tool, mode="full")

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        tool = self._tool
        if not tool:
            return items
        for label, mode in [
            ("Full Screen", "full"),
            ("Window", "window"),
            ("Region", "region"),
        ]:
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _w, t=tool, m=mode: _run(tool=t, mode=m))
            items.append(mi)
        return items
