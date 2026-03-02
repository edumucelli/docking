"""Screenshot applet -- full screen, window, or region capture."""

from __future__ import annotations

import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="screenshot"), applet_id=str(AppletId.SCREENSHOT))


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
        _log.bind(action=f"screenshot_{mode}").warning(f"Failed to run {cmd}: {exc}")


class ScreenshotApplet(Applet):
    """Capture screenshots via the best available tool.

    Left-click takes a full-screen capture. Right-click menu offers
    full screen, active window, and region selection modes.
    Auto-detects mate-screenshot, gnome-screenshot, or scrot.
    """

    id = AppletId.SCREENSHOT
    name = "Screenshot"
    icon_name = "applets-screenshooter"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._tool = _detect_tool()
        if not self._tool:
            _log.bind(action="detect_tool").warning(
                f"No screenshot tool found ({', '.join(t.command for t in _TOOLS)})"
            )
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)

        _draw_screenshot_icon(cr=cr, size=size)

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

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


def _rounded_rect_path(
    cr: cairo.Context, x: float, y: float, w: float, h: float, r: float
) -> None:
    """Add a rounded-rectangle path to the Cairo context."""
    r = max(0.0, min(r, min(w, h) / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -0.5 * math.pi, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _draw_soft_shadow(
    cr: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    r: float,
    dx: float,
    dy: float,
    spread: float,
    passes: int,
) -> None:
    """Approximate a blur shadow with multiple expanded rounded-rect fills."""
    base_alpha = 0.14
    for i in range(passes):
        t = (i + 1) / passes
        expand = spread * t
        alpha = base_alpha * (1.0 - t)
        cr.save()
        cr.set_source_rgba(0.0, 0.0, 0.0, alpha)
        _rounded_rect_path(
            cr=cr,
            x=x - expand + dx,
            y=y - expand + dy,
            w=w + (2 * expand),
            h=h + (2 * expand),
            r=r + expand,
        )
        cr.fill()
        cr.restore()


def _draw_screenshot_icon(cr: cairo.Context, size: int) -> None:
    tile_margin = 0.06 * size
    tile_size = size - (2 * tile_margin)
    tile_x = (size - tile_size) / 2.0
    tile_y = (size - tile_size) / 2.0
    tile_w = tile_size
    tile_h = tile_size
    tile_radius = 0.10 * tile_w

    # Soft drop shadow.
    _draw_soft_shadow(
        cr=cr,
        x=tile_x,
        y=tile_y,
        w=tile_w,
        h=tile_h,
        r=tile_radius,
        dx=0.0,
        dy=0.0,
        spread=0.04 * tile_w,
        passes=12,
    )

    # Main blue tile.
    _rounded_rect_path(cr=cr, x=tile_x, y=tile_y, w=tile_w, h=tile_h, r=tile_radius)
    cr.set_source_rgb(0x62 / 255.0, 0x8C / 255.0, 0xF6 / 255.0)
    cr.fill()

    # Inner dashed rounded rectangle.
    inset = 0.21 * tile_w
    inner_x = tile_x + inset
    inner_y = tile_y + inset
    inner_w = tile_w - (2 * inset)
    inner_h = inner_w
    inner_r = 0.09 * tile_w
    stroke_w = max(1.0, 0.032 * tile_w)

    # Second blue tone inside the crop contour.
    _rounded_rect_path(cr=cr, x=inner_x, y=inner_y, w=inner_w, h=inner_h, r=inner_r)
    cr.set_source_rgb(0x86 / 255.0, 0xA6 / 255.0, 0xF8 / 255.0)
    cr.fill()

    # Subtle lower-half opacity ramp across all blue areas.
    cr.save()
    _rounded_rect_path(cr=cr, x=tile_x, y=tile_y, w=tile_w, h=tile_h, r=tile_radius)
    cr.clip()
    mid_y = tile_y + (tile_h * 0.48)
    shade = cairo.LinearGradient(0, mid_y, 0, tile_y + tile_h)
    shade.add_color_stop_rgba(0.0, 0.0, 0.0, 0.0, 0.0)
    shade.add_color_stop_rgba(0.2, 0.0, 0.0, 0.0, 0.04)
    shade.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.10)
    cr.set_source(shade)
    cr.rectangle(tile_x, mid_y, tile_w, tile_h - (mid_y - tile_y))
    cr.fill()
    cr.restore()

    cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
    cr.set_line_width(stroke_w)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    dash_len = stroke_w * 2.0
    cr.set_dash([dash_len, dash_len], 0)
    _rounded_rect_path(cr=cr, x=inner_x, y=inner_y, w=inner_w, h=inner_h, r=inner_r)
    cr.stroke()
    cr.set_dash([])

    # Bottom-right solid "+" marker at the contour edge intersection.
    offset = stroke_w * 0.55
    cross_x = inner_x + inner_w + (stroke_w / 2.0) - offset
    cross_y = inner_y + inner_h + (stroke_w / 2.0) - offset
    cross_len = 0.14 * tile_w
    half = cross_len / 2.0

    cr.set_line_cap(cairo.LINE_CAP_BUTT)
    cr.set_line_join(cairo.LINE_JOIN_MITER)
    cr.move_to(cross_x - half, cross_y)
    cr.line_to(cross_x + half, cross_y)
    cr.stroke()

    cr.move_to(cross_x, cross_y - half)
    cr.line_to(cross_x, cross_y + half)
    cr.stroke()
