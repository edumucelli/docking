# Applets

Applets are custom widgets that live in the dock alongside application icons. Enable them via right-click on the dock background -> **Applets**. 26 built-in applets are available.

## Architecture

Docking applets follow a small, testable architecture:

- `docking/applets/base.py` defines the common applet lifecycle and UI hooks:
  - `create_icon(size)`
  - `on_clicked()`
  - `on_scroll(direction_up)`
  - `get_menu_items()`
  - optional `start(notify=...)` / `stop()`
- Most applets are organized as a package with three modules:
  - `state.py`: pure logic, parsing, command/state helpers (easy to unit test)
  - `render.py`: Cairo/icon rendering helpers (no applet lifecycle logic)
  - `applet.py`: GTK/Wnck/Gio wiring, timers, click/scroll/menu behavior
- Package `__init__.py` re-exports public symbols used by the registry/tests.
- Applet classes are loaded through `docking/applets/__init__.py:get_registry()`.
- Each applet declares a stable identity via `AppletId` from `docking/applets/identity.py`.

This split keeps runtime behavior in one place while making parsers/rendering highly testable without a live desktop session.

---

## Clock

<img src="../docking/assets/icons/applets/clock.png" alt="Clock" width="48">

Analog or digital clock face. The analog mode uses SVG layers for a realistic clock face with hour/minute hands.

**Right-click options:**
- **Digital Clock** -- switch between analog and digital display
- **24-Hour Clock** -- toggle 12/24-hour format
- **Show Date** -- show date below time (digital mode only)

**Preferences stored:** `show_digital`, `show_military`, `show_date`

## Trash

<img src="../docking/assets/icons/applets/trash.png" alt="Trash" width="48">

Shows the current state of the system trash. Icon switches between empty and full automatically via file monitoring.

**Click:** Open trash folder in file manager
**Right-click options:**
- **Open Trash** -- open in file manager
- **Empty Trash** -- permanently delete all trashed items (uses Caja/Nautilus DBus when available)

## Desktop

<img src="../docking/assets/icons/applets/desktop.png" alt="Desktop" width="48">

Toggle "show desktop" mode -- minimizes or restores all windows.

**Click:** Toggle show/hide all windows

## System Monitor

<img src="../docking/assets/icons/applets/systemmonitor.png" alt="System Monitor" width="48">

Circular gauge showing real-time CPU and memory usage. The fill color shifts from green (idle) to red (busy). A white arc around the edge shows memory usage.

**Tooltip:** `CPU: 23.5% | Mem: 67.2% | Temp: 54.0°C` when CPU temperature is available

**Update interval:** 1 second (with 3% CPU / 1% memory threshold to avoid excessive redraws)

**Temperature sources:** Linux sysfs first, then common sensor tools such as `sensors`, `vcgencmd`, and `acpi` when installed

## Battery

<img src="../docking/assets/icons/applets/battery.png" alt="Battery" width="48">

Shows battery charge level using standard FreeDesktop icons. Reads from `/sys/class/power_supply/BAT0/`. Icon changes based on charge level (full, good, low, caution, empty) and charging state.

**Tooltip:** Shows percentage (e.g. "85%") or "No battery"

**Update interval:** 60 seconds

## Brightness

<img src="../docking/assets/icons/applets/brightness.png" alt="Brightness" width="48">

Screen brightness control via `xrandr`. Auto-detects the primary display output and tracks live brightness value.

**Click:** Reset brightness to 100%
**Scroll:** Adjust brightness by small steps
**Right-click options:**
- **Show Level** -- toggle percentage text overlay on icon

**Tooltip:** `Brightness: N%`

**Update interval:** 5 seconds

## Weather

<img src="../docking/assets/icons/applets/weather.png" alt="Weather" width="48">

Shows current weather and air quality for a selected city with a 5-day forecast. Uses the [Open-Meteo](https://open-meteo.com/) weather and air quality APIs with automatic caching and retry.

**Click:** Open forecast in browser
**Right-click options:**
- **Show Temperature** -- toggle temperature overlay on icon
- **Change City...** -- opens search dialog with autocomplete (48,000 cities)

**Tooltip:** Bold city header + current conditions + air quality + daily forecast with icons:
```
Contagem, Brazil
29 C, Clear sky
Air: Good
Mon: 25/29 C, Partly cloudy
Tue: 28/32 C, Rain
```

**Preferences stored:** `city_display`, `lat`, `lng`, `show_temperature`

**Update interval:** 5 minutes (shared between API cache and polling timer)

## Moon

<img src="../docking/assets/icons/applets/moon.png" alt="Moon" width="48">

Moon phase applet with Cairo-rendered moon disc and illumination shading. Fetches phase data asynchronously and falls back gracefully while loading.

**Click:** Refresh moon data now
**Right-click options:**
- **Show Phase Name** -- toggle phase label overlay on icon
- **Refresh** -- force a refresh

**Tooltip:** Multi-line phase summary with illumination percentage and description

**Update interval:** 6 hours

## Clippy

<img src="../docking/assets/icons/applets/clippy.png" alt="Clippy" width="48">

Clipboard history manager. Monitors the system clipboard and stores the last 15 text entries.

**Click:** Copy the currently selected clip back to the clipboard
**Scroll:** Cycle through clipboard history (tooltip updates instantly)
**Right-click:** List of all clips (newest first), click to copy. "Clear" to empty history.

**Preferences stored:** `max_entries`

## Color Picker

<img src="../docking/assets/icons/applets/colorpicker.png" alt="Color Picker" width="48">

Eyedropper color picker. Click enters fullscreen pick mode, samples a pixel color, copies hex value to clipboard, and updates the icon swatch.

**Click:** Start pick mode and sample next clicked pixel
**Right-click options:**
- **Copy #RRGGBB** -- copy current sampled value
- **Show Hex** -- toggle hex label overlay on icon

**Tooltip:** Current sampled hex value

**Preferences stored:** `show_hex`, `r`, `g`, `b`, `hex`

## Applications

<img src="../docking/assets/icons/applets/applications.png" alt="Applications" width="48">

Categorized application launcher. Groups all installed `.desktop` applications by FreeDesktop category (Multimedia, Development, Internet, etc.) with icons.

**Right-click:** Categorized submenus with application icons. Click an app to launch it.

## Network

<img src="../docking/assets/icons/applets/network.png" alt="Network" width="48">

Shows WiFi signal strength or wired connection status, with live upload/download speed overlay.

**Tooltip:**
```
WiFi: MyNetwork (82%)
IP: 192.168.1.42
down-arrow 1.2 MB/s  up-arrow 350 KB/s
```

**Right-click:** Connection info (read-only)

**Data sources:**
- NetworkManager (via NM 1.0) for connection state, SSID, signal strength
- `/proc/net/dev` for traffic counters

**Update interval:** 2 seconds for traffic, instant for connection state changes (NM signals)

## Bluetooth

BlueZ-based Bluetooth manager applet for quick adapter/device control from the dock.

**Click:** Toggle Bluetooth power for the active adapter
**Right-click options:**
- **Bluetooth On** -- power toggle
- **Continuous Discovery** -- keeps discovery active while enabled
- **Adapter** -- switch active adapter on multi-adapter systems
- **Connected / Paired / Discovered Devices** -- per-device actions:
  connect/disconnect, pair, remove pairing, trust toggle

**Tooltip:** adapter state, connected/paired counts, discovery status, optional battery line
**Badge:** connected device count

**Backends:**
- BlueZ DBus (`org.bluez`) for adapter/device operations
- `bluetoothctl` fallback for pairing when DBus pair fails

**Note:** if another Bluetooth app owns an active discovery session, BlueZ may
block power-off (`org.bluez.Error.Busy`) until that external scan stops.

**Update interval:** 2 seconds poll + discovery keepalive

## Power Profiles

Power profile applet for quick laptop/handheld mode switching.

**Click:** Cycle to next available profile
**Right-click options:**
- **Select Profile** -- radio selector for available profiles
- **Power Saver / Balanced / Performance** -- set active profile

**Tooltip:** current profile, available profiles, and backend limitation reason (if any)

**Backend chain (auto-detected):**
- `power-profiles-daemon` via DBus `net.hadess.PowerProfiles` (preferred)
- `tuned-adm` fallback (profile-mapped)
- `tlp` fallback (`ac`/`bat`/`start` mapping)

## Notifications

Notification center applet with a compact status icon, Do Not Disturb toggle, and pending badge when supported.

**Click:** Toggle Do Not Disturb on/off
**Right-click options:**
- **Do Not Disturb** -- toggle notification pause state
- **Pending: N** -- pending notifications (when backend exposes queue size)
- **Clear Notifications** -- clear notification history (when backend supports it)

**Backends:**
- `dunstctl` (Dunst): pause state, pending count, and clear-history action
- `gsettings` (GNOME): pause state via `org.gnome.desktop.notifications show-banners`

**Update interval:** 2 seconds

## Music

Media controller applet with album-art icon rendering. Uses MPRIS over DBus first, then playerctl fallback for controls when needed.

Tested with **VLC**, **Clementine**, **Amberol**, and **Recordbox**. Should work with any MPRIS-compatible player.

**Click:** Play/pause
**Scroll:** Player volume +/-5%
**Right-click options:**
- **Previous** / **Play** / **Pause** / **Next**
- **Volume Up** / **Volume Down**

**Tooltip:** multiline summary, e.g. `Artist - Title`, `Album: ...`, `Vol N%`

## Session

<img src="../docking/assets/icons/applets/session.png" alt="Session" width="48">

Lock, logout, suspend, restart, or shut down via `loginctl`/`systemctl`.

**Click:** Lock screen
**Right-click options:**
- **Lock Screen** -- `loginctl lock-session`
- **Log Out** -- `loginctl terminate-session`
- **Suspend** -- `systemctl suspend`
- **Restart** -- `systemctl reboot`
- **Shut Down** -- `systemctl poweroff`

## Calendar

<img src="../docking/assets/icons/applets/calendar.png" alt="Calendar" width="48">

Shows today's date as a calendar page icon with red header (weekday) and day number.

**Click:** Toggle a GtkCalendar popup
**Tooltip:** Full date (e.g. "Tuesday, February 25")

**Update interval:** 30 seconds (refreshes icon at midnight)

## Workspaces

<img src="../docking/assets/icons/applets/workspaces.png" alt="Workspaces" width="48">

Workspace switcher with a visual grid icon. Active workspace is highlighted in blue.

**Click:** Cycle to next workspace
**Scroll:** Switch workspace up/down
**Right-click options:** Radio list of all workspaces

**Tooltip:** Active workspace name

## Screenshot

<img src="../docking/assets/icons/applets/screenshot.png" alt="Screenshot" width="48">

Capture screenshots via the best available tool. Auto-detects mate-screenshot, gnome-screenshot, xfce4-screenshooter, spectacle, flameshot, or scrot.

**Click:** Full-screen capture
**Right-click options:**
- **Full Screen** -- capture entire screen
- **Window** -- capture active window
- **Region** -- interactive area selection
- **Full Screen in 3s/5s/7s/9s** -- delayed full-screen capture

## Volume

<img src="../docking/assets/icons/applets/volume.png" alt="Volume" width="48">

System volume control. Auto-detects pactl (PulseAudio/PipeWire) or amixer (ALSA). Icon switches between muted/low/medium/high based on level.

**Click:** Toggle mute
**Scroll:** Adjust volume +/-5%
**Tooltip:** `Volume: 75%` or `Muted`

**Update interval:** 1 second (refreshes only on change)

## Pomodoro

<img src="../docking/assets/icons/applets/pomodoro.png" alt="Pomodoro" width="48">

Pomodoro timer with a flat tomato icon. Auto-cycles through work/break phases with configurable durations. Triggers urgent bounce+glow on phase transitions.

**Click:** Start/pause toggle
**Right-click options:**
- **Reset** -- back to idle
- **Work duration** -- 15/25/30/45 min presets
- **Break duration** -- 5/10 min presets
- **Long break duration** -- 15/20/30 min presets

**Preferences stored:** `work`, `break_`, `long_break`

## Separator

Transparent gap divider between dock items. Supports multiple instances -- each with independent, persistent size.

**Scroll:** Adjust gap width (+/-2px, range 2-48px)
**Right-click options:**
- **Increase Gap** / **Decrease Gap**
- **Remove from Dock**

Added via right-click on dock background -> **Add Separator** (inserts at click position).

## Hydration

<img src="../docking/assets/icons/applets/hydration.png" alt="Hydration" width="48">

Water drop icon that drains over a configurable interval, reminding you to drink water. Click to refill. Triggers urgent bounce when empty.

**Click:** Refill (log a drink)
**Right-click options:**
- **Show Timer** -- toggle countdown overlay on icon
- **Interval presets** -- 15/30/45/60/90 min

**Preferences stored:** `interval`, `show_timer`

## Quote

Quote/joke applet inspired by the original Cairo-Dock Quote plugin. Ships with local fallback quotes and supports online refresh from active sources.

**Click:** Show next quote
**Right-click options:**
- **Next Quote**
- **Copy Quote** -- copy current quote to clipboard
- **Refresh from Web**
- **Source** -- switch source (Quotationspage, Qdb, Danstonchat, Viedemerde, Fmylife, Vitadimerda, Chucknorrisfactsfr)

**Preferences stored:** `source`

## Ambient

<img src="../docking/assets/icons/applets/ambient.png" alt="Ambient" width="48">

Looping ambient soundscape player. Bundled with 7 CC0/Public Domain nature sounds plus procedural white/pink noise via GStreamer.

**Click:** Toggle play/stop
**Scroll:** Adjust volume +/-10%
**Right-click:** Sound selection (Birds, Boat, Coffee Shop, Fireplace, Stream, Summer Night, Wind, White Noise, Pink Noise)

**Preferences stored:** `sound`, `volume`

---

## Writing Custom Applets

Applets extend the `Applet` abstract base class in `docking/applets/base.py`:

```python
from docking.applets.base import Applet, load_theme_icon
from docking.applets.identity import AppletId

class MyApplet(Applet):
    id = AppletId.MY_APPLET  # add enum entry in identity.py
    name = "My Applet"       # display name in menus
    icon_name = "my-icon"    # fallback icon

    def create_icon(self, size):
        """Render your icon as a GdkPixbuf at the given size."""
        return load_theme_icon(name="my-icon", size=size)

    def on_clicked(self):
        """Handle left-click."""

    def on_scroll(self, direction_up):
        """Handle scroll wheel."""

    def get_menu_items(self):
        """Return list of Gtk.MenuItem for right-click menu."""
        return []

    def start(self, notify):
        """Called after dock is ready. Start timers/monitors."""
        super().start(notify)

    def stop(self):
        """Cleanup. Called on removal or shutdown."""
        super().stop()
```

**Key patterns:**
- Call `self.refresh_icon()` to trigger a redraw after state changes
- Use `self.save_prefs(dict)` / `self.load_prefs()` for persistent preferences
- Use `load_theme_icon(name, size)` for GTK theme icons
- Use `load_theme_icon_centered(name, size)` for non-square icons (e.g. battery)
- For Cairo rendering: create a surface, draw, return via `Gdk.pixbuf_get_from_surface()`
- For background work: use `threading.Thread` + `GLib.idle_add()` to dispatch results to main thread

Recommended file layout:

```text
docking/applets/my_applet/
  __init__.py   # re-export MyApplet (+ public helpers if needed)
  applet.py     # GTK wiring and lifecycle
  state.py      # pure state/logic helpers
  render.py     # icon rendering helpers
```

Register your applet in `docking/applets/__init__.py` (`get_registry()`):

```python
from docking.applets.my_applet import MyApplet
from docking.applets.identity import AppletId

return {
    ...
    AppletId.MY_APPLET: MyApplet,
}
```

**Design principle:** Complex logic is extracted as pure functions (no GTK dependency) so tests run fast without a display server. GTK-dependent tests use lightweight mocks.
