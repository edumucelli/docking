#!/usr/bin/env python3
"""Generate static catalog PNG assets for dock applets.

This is a one-off/offline asset generator for the Add Applet menu and the
Preferences > Applets catalog. It does not instantiate live applets. Instead it
renders a stable representative icon for each applet using the same Cairo/theme
render helpers used by the dock itself.

Why this script exists
======================

Some applets perform backend work in ``__init__`` (DBus, Wnck, xrandr, audio,
network, etc.). Instantiating every applet just to obtain a catalog icon is not
safe or deterministic. The catalog only needs a recognizable, dock-style icon,
not a live runtime state snapshot.

So this script uses explicit canonical snapshots:
- pure Cairo render helpers when available,
- theme-icon based render helpers when that is how the dock icon is drawn,
- representative default values for dynamic/status applets.

The generated PNGs are then treated as the sole icon source for catalog UI.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.ambient.render import render_icon as render_ambient
from docking.applets.applications.render import create_icon as render_applications
from docking.applets.battery.render import render_icon as render_battery
from docking.applets.battery.state import BatteryState
from docking.applets.bluetooth.render import create_bluetooth_icon
from docking.applets.bookmarks.render import render_icon as render_bookmarks
from docking.applets.brightness.render import create_icon as render_brightness
from docking.applets.calendar.render import render_icon as render_calendar
from docking.applets.calendar.state import snapshot_from
from docking.applets.clippy.render import create_icon as render_clippy
from docking.applets.clock.render import render_icon as render_clock
from docking.applets.colorpicker.render import create_icon as render_colorpicker
from docking.applets.cpumonitor.render import render_icon as render_cpumonitor
from docking.applets.desktop.render import create_icon as render_desktop
from docking.applets.hydration.render import render_icon as render_hydration
from docking.applets.hydration.state import HydrationState
from docking.applets.identity import AppletId
from docking.applets.keyboardlayout.render import render_icon as render_keyboardlayout
from docking.applets.moon.offline import fetch_moon_offline
from docking.applets.moon.render import create_icon as render_moon
from docking.applets.moon.state import phase_name
from docking.applets.music.render import create_music_icon
from docking.applets.network.render import create_icon as render_network
from docking.applets.notifications.render import create_notifications_icon
from docking.applets.pet.render import render_icon as render_pet
from docking.applets.pet.state import PetState
from docking.applets.pomodoro.render import render_icon as render_pomodoro
from docking.applets.pomodoro.state import PomodoroState
from docking.applets.powerprofiles.render import create_power_profiles_icon
from docking.applets.quicknote.render import render_icon as render_quicknote
from docking.applets.quote.render import draw_bulb_icon
from docking.applets.recentfiles.render import render_icon as render_recentfiles
from docking.applets.screenshot.applet import _draw_screenshot_icon
from docking.applets.session.render import create_session_icon
from docking.applets.stretchcoach.render import render_icon as render_stretchcoach
from docking.applets.stretchcoach.state import StretchCoachState
from docking.applets.trash.render import create_trash_icon
from docking.applets.trivia.render import draw_trivia_icon
from docking.applets.volume.render import create_volume_icon
from docking.applets.weather.render import create_icon as render_weather
from docking.applets.workspaces.render import _render_grid

ICON_SIZE = 64
OUT_DIR = (
    Path(__file__).resolve().parent.parent
    / "docking"
    / "assets"
    / "icons"
    / "applets"
)


def _surface_to_pixbuf(*, size: int, draw_fn) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    draw_fn(cr, size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _save_png(*, applet_id: AppletId, pixbuf: GdkPixbuf.Pixbuf) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{applet_id.value}.png"
    pixbuf.savev(str(out_path), "png", [], [])
    print(out_path)


def _moon_pixbuf(*, size: int) -> GdkPixbuf.Pixbuf | None:
    moon = fetch_moon_offline()
    waning = (
        "after full" in moon.description.lower()
        or "before last" in moon.description.lower()
        or "before new" in moon.description.lower()
        or "after last" in moon.description.lower()
    )
    label = phase_name(
        illumination=moon.illumination,
        description=moon.description,
    )
    return render_moon(
        size=size,
        illumination=moon.illumination,
        waning=waning,
        label=label,
    )


def _quote_pixbuf(*, size: int) -> GdkPixbuf.Pixbuf | None:
    return _surface_to_pixbuf(
        size=size,
        draw_fn=lambda cr, s: draw_bulb_icon(cr=cr, size=s),
    )


def _screenshot_pixbuf(*, size: int) -> GdkPixbuf.Pixbuf | None:
    return _surface_to_pixbuf(
        size=size,
        draw_fn=lambda cr, s: _draw_screenshot_icon(cr=cr, size=s),
    )


def _trivia_pixbuf(*, size: int) -> GdkPixbuf.Pixbuf | None:
    return _surface_to_pixbuf(
        size=size,
        draw_fn=lambda cr, s: draw_trivia_icon(cr=cr, size=s),
    )


def _workspaces_pixbuf(*, size: int) -> GdkPixbuf.Pixbuf | None:
    return _surface_to_pixbuf(
        size=size,
        draw_fn=lambda cr, s: _render_grid(cr=cr, size=s, count=4, active_num=0),
    )


def _build_pixbufs(*, size: int) -> dict[AppletId, GdkPixbuf.Pixbuf | None]:
    now = time.localtime()
    cal_snapshot = snapshot_from()
    return {
        AppletId.AMBIENT: render_ambient(size=size),
        AppletId.APPLICATIONS: render_applications(size=size),
        AppletId.BATTERY: render_battery(
            size=size,
            state=BatteryState(icon_name="battery-good", capacity=75),
        ),
        AppletId.BOOKMARKS: render_bookmarks(size=size, count=3),
        AppletId.BLUETOOTH: create_bluetooth_icon(
            size=size,
            available=True,
            powered=True,
            discovering=False,
            connected_devices=0,
        ),
        AppletId.BRIGHTNESS: render_brightness(
            size=size,
            brightness=0.70,
            show_level=False,
        ),
        AppletId.CALENDAR: render_calendar(size=size, snapshot=cal_snapshot),
        AppletId.CLIPPY: render_clippy(size=size),
        AppletId.CLOCK: render_clock(
            size=size,
            now=now,
            show_digital=False,
            show_military=False,
            show_date=False,
        ),
        AppletId.COLORPICKER: render_colorpicker(
            size=size,
            r=0.5,
            g=0.5,
            b=0.5,
            hex_label=None,
        ),
        AppletId.CPUMONITOR: render_cpumonitor(size=size, cpu=0.42, mem=0.28),
        AppletId.DESKTOP: render_desktop(size=size),
        AppletId.HYDRATION: render_hydration(size=size, state=HydrationState()),
        AppletId.KEYBOARDLAYOUT: render_keyboardlayout(size=size, label="US"),
        AppletId.MOON: _moon_pixbuf(size=size),
        AppletId.MUSIC: create_music_icon(
            size=size,
            playback_status="Stopped",
            album_art=None,
            volume_percent=60,
            available=False,
        ),
        AppletId.NETWORK: render_network(
            size=size,
            is_connected=True,
            is_wifi=True,
            signal_strength=75,
            rx_speed=0.0,
            tx_speed=0.0,
            speed_overlay="none",
        ),
        AppletId.NOTIFICATIONS: create_notifications_icon(
            size=size,
            available=True,
            paused=False,
            badge_count=0,
            activity=False,
        ),
        AppletId.PET: render_pet(size=size, state=PetState()),
        AppletId.POMODORO: render_pomodoro(size=size, state=PomodoroState()),
        AppletId.POWERPROFILES: create_power_profiles_icon(
            size=size,
            profile="balanced",
            available=True,
        ),
        AppletId.QUICKNOTE: render_quicknote(size=size, has_content=True),
        AppletId.QUOTE: _quote_pixbuf(size=size),
        AppletId.RECENTFILES: render_recentfiles(size=size, has_files=True),
        AppletId.SCREENSHOT: _screenshot_pixbuf(size=size),
        AppletId.SESSION: create_session_icon(size=size),
        AppletId.STRETCHCOACH: render_stretchcoach(
            size=size,
            state=StretchCoachState(),
        ),
        AppletId.TRIVIA: _trivia_pixbuf(size=size),
        AppletId.TRASH: create_trash_icon(size=size, item_count=0),
        AppletId.VOLUME: create_volume_icon(size=size, volume=60, muted=False),
        AppletId.WEATHER: render_weather(
            size=size,
            weather=None,
            show_temperature=True,
        ),
        AppletId.WORKSPACES: _workspaces_pixbuf(size=size),
    }


def main() -> int:
    generated = _build_pixbufs(size=ICON_SIZE)
    missing = []
    for applet_id in AppletId:
        if applet_id == AppletId.SEPARATOR:
            continue
        pixbuf = generated.get(applet_id)
        if pixbuf is None:
            missing.append(applet_id.value)
            continue
        _save_png(applet_id=applet_id, pixbuf=pixbuf)
    if missing:
        print("Missing icons:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
