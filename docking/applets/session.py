"""Session applet -- lock, logout, suspend, restart, shutdown."""

from __future__ import annotations

import math
import subprocess
from typing import TYPE_CHECKING, NamedTuple

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="session")
TWO_PI = 2 * math.pi


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

    id = AppletId.SESSION
    name = "Session"
    icon_name = "system-log-out"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)

        cx = size / 2
        cy = size / 2
        stroke = (0.23, 0.58, 0.98)
        fill = (0.68, 0.80, 0.97)
        ring_r = size * 0.42

        # Filled circular background.
        cr.set_source_rgb(*fill)
        cr.arc(cx, cy, ring_r, 0, TWO_PI)
        cr.fill()

        # Outer ring (thinner stroke).
        cr.set_source_rgb(*stroke)
        cr.set_line_width(max(1.2, size * 0.05))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.arc(cx, cy, ring_r, 0, TWO_PI)
        cr.stroke()

        # Head.
        cr.arc(cx, cy - size * 0.11, size * 0.12, 0, TWO_PI)
        cr.set_source_rgb(0.79, 0.88, 0.99)
        cr.fill_preserve()
        cr.set_source_rgb(*stroke)
        cr.set_line_width(max(1.2, size * 0.03))
        cr.stroke()

        # Shoulders / torso curve connected to the outer ring.
        left_angle = math.radians(145)
        right_angle = math.radians(35)
        left_x = cx + ring_r * math.cos(left_angle)
        left_y = cy + ring_r * math.sin(left_angle)
        right_x = cx + ring_r * math.cos(right_angle)
        right_y = cy + ring_r * math.sin(right_angle)

        cr.set_source_rgb(*stroke)
        cr.set_line_width(max(1.8, size * 0.055))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(left_x, left_y)
        cr.curve_to(
            cx - size * 0.16,
            cy + size * 0.03,
            cx + size * 0.16,
            cy + size * 0.03,
            right_x,
            right_y,
        )
        cr.stroke()

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

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
