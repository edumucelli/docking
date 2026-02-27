"""Session applet -- lock, logout, suspend, restart, shutdown."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, NamedTuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="session")


class SessionAction(NamedTuple):
    """A session/power action with its shell command."""

    label: str
    command: list[str]


_ACTIONS: list[SessionAction] = [
    SessionAction("Lock Screen", ["loginctl", "lock-session"]),
    SessionAction("Log Out", ["loginctl", "terminate-session", ""]),
    SessionAction("Suspend", ["systemctl", "suspend"]),
    SessionAction("Restart", ["systemctl", "reboot"]),
    SessionAction("Shut Down", ["systemctl", "poweroff"]),
]


def _run(cmd: list[str]) -> None:
    """Run a session/power command, logging failures."""
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        _log.warning("Failed to run %s: %s", cmd, exc)


class SessionApplet(Applet):
    """Provides session and power management actions.

    Left-click locks the screen. Right-click menu offers logout,
    suspend, restart, and shutdown via systemctl/loginctl.
    """

    id = "session"
    name = "Session"
    icon_name = "system-log-out"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return load_theme_icon(name="system-log-out", size=size)

    def on_clicked(self) -> None:
        """Lock screen on left-click."""
        _run(cmd=["loginctl", "lock-session"])

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        for label, cmd in _ACTIONS:
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _w, c=cmd: _run(cmd=c))
            items.append(mi)
        return items
