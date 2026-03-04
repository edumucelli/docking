# Docking

[![CI](https://github.com/edumucelli/docking/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/edumucelli/docking/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/edumucelli/docking?display_name=tag)](https://github.com/edumucelli/docking/releases)
[![Coverage](https://codecov.io/gh/edumucelli/docking/branch/master/graph/badge.svg)](https://codecov.io/gh/edumucelli/docking)
[![Last commit](https://img.shields.io/github/last-commit/edumucelli/docking)](https://github.com/edumucelli/docking/commits/master)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)

<img src="images/docking-header.png" alt="Docking" height="48" style="display:block; margin:0 auto;">

A lightweight, feature-rich dock for Linux written in Python with GTK 3 and Cairo. Inspired by [Plank](https://launchpad.net/plank) and [Cairo-Dock](https://github.com/Cairo-Dock), with an extensible applet system for custom widgets.
![all.gif](images/all.gif)

## Features

### Core
- Pinned application launchers with click-to-launch
- Running application indicators (dots) with active window glow
- Parabolic icon zoom on hover with per-icon displacement
- 3D shelf background with gradient fill and inner highlight bevel
- Smart focus: click to focus/minimize, Ctrl+click or middle-click to launch new instance
- Desktop actions (quicklists) from `.desktop` files in the right-click menu
- Multi-position support: bottom, top, left, right
- Multi-monitor support with runtime monitor selection via right-click menu (**Display** submenu appears when multiple monitors are available)

### Visual Effects
- **Hover lighten** -- additive brightness fade on hovered icon
- **Click darken** -- brief sine-curve pulse on click
- **Launch bounce** -- two-bounce momentum decay animation when launching
- **Urgent bounce** -- single bounce when app demands attention
- **Urgent glow** -- pulsing radial glow at screen edge when dock is hidden and an app demands attention
- **Active window glow** -- color-matched gradient using icon's dominant color
- **Cascade hide** -- shelf slides faster than icons for a layered effect
- **Smooth zoom decay** -- icons shrink while dock slides away (no snap)

### Window Previews
- Thumbnails of running windows on hover (X11 foreign window capture)
- Click thumbnail to activate a specific window
- Dock stays visible while browsing previews
- Toggleable via right-click menu

### Drag and Drop
- Drag to reorder icons (with slide animation)
- Drag `.desktop` files from file manager to add icons (gap insertion effect)
- Drag icons off dock to remove (poof smoke animation)
- **Lock Icons** -- toggle via right-click menu to prevent reordering, adding, and removing icons

### Auto-hide
- Cubic easing animation (ease-in hide, ease-out show)
- Configurable delay (0ms default = instant, like Plank)
- 2px trigger strip at screen edge when hidden
- Struts update instantly on toggle (windows resize immediately)

### Applets
Extensible plugin system for custom dock widgets. 26 built-in applets:

| Applet | Description |
|--------|-------------|
| **Clock** | Analog or digital clock with 12/24h modes |
| **Trash** | Trash can with real-time monitoring |
| **Desktop** | Toggle show desktop |
| **CPU Monitor** | Circular gauge with CPU and memory usage |
| **Battery** | Battery charge level with charging indicator |
| **Weather** | Current weather, air quality, and 5-day forecast |
| **Clippy** | Clipboard history manager |
| **Applications** | Categorized application launcher |
| **Network** | WiFi signal strength and traffic speeds |
| **Bluetooth** | Full BlueZ manager with device connect/pair controls |
| **Power Profiles** | Performance/Balanced/Power Saver profile selector |
| **Notifications** | Do Not Disturb toggle with pending badge |
| **Music** | Media controls with album-art icon |
| **Session** | Lock, logout, suspend, restart, shutdown |
| **Calendar** | Date icon with popup calendar |
| **Workspaces** | Workspace switcher with grid icon |
| **Screenshot** | Capture full screen, window, or region |
| **Volume** | Scroll to adjust, click to mute |
| **Pomodoro** | Tomato timer with auto-cycling work/break phases |
| **Separator** | Transparent gap divider (multiple instances, scroll to resize) |
| **Hydration** | Water drop reminder - drains over time, click to refill |
| **Quote** | Quote/joke applet with source switching and clipboard copy |
| **Ambient** | Looping nature sounds and white/pink noise via GStreamer |
| **Color Picker** | Eyedropper tool - click to sample pixel, copies hex to clipboard |
| **Brightness** | Screen brightness control via xrandr, scroll to adjust |
| **Moon** | Lunar phase display with Cairo rendering and astronomical fallback |

See [Applet Documentation](#applets-1) below for details on each applet.

## Requirements

- Linux with X11
- Python 3.10+
- System packages (Ubuntu/Debian):

```bash
sudo apt install \
  python3-venv \
  python3-gi python3-gi-cairo \
  gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 gir1.2-wnck-3.0 gir1.2-pango-1.0 \
  gir1.2-nm-1.0 gir1.2-gstreamer-1.0 \
  libcairo2-dev libgirepository1.0-dev pkg-config
```

## Installation

Prebuilt latest release packages are also available on [GitHub Releases](https://github.com/edumucelli/docking/releases), you can download them directly below.
- [AppImage](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.AppImage)
- [Debian .deb](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-all.deb)
- [RPM](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.rpm)
- [Flatpak](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.flatpak)
- [Snap](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.snap)
- [Arch package](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.pkg.tar.zst)
- Nix [store path](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64-nix-store-path.txt) and [output tarball](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64-nix-output.tar.gz)

```bash
# Clone
git clone https://github.com/edumucelli/docking.git
cd docking

# Create venv with access to system GI bindings
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

# Install with dependencies
pip install -e ".[dev]"
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python /usr/bin/python3 --system-site-packages .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific module
pytest tests/applets/test_clock.py -v

# Coverage report
pytest tests/ -v --cov=docking --cov-report=term-missing
```

## Packages

### Building a .deb package

```bash
# Install build dependencies
sudo apt install python3-all python3-setuptools python3-wheel python3-pip \
  debhelper dh-python pybuild-plugin-pyproject

# Build
./packaging/deb/build.sh

# Install generated package
sudo dpkg -i ../docking_*_all.deb
sudo apt-get -f install
```

### Building a Flatpak bundle

```bash
# Install tooling
sudo apt install flatpak flatpak-builder

# Build bundle
./packaging/flatpak/build.sh

# Install and run locally
flatpak install --user ./artifacts/org.docking.Docking.flatpak
flatpak run org.docking.Docking
```

### Building a Snap package

```bash
# Install tooling
sudo apt install snapcraft

# Build snap package
snapcraft --destructive-mode --project-dir packaging/snap --output artifacts/docking.snap

# Install locally
sudo snap install --dangerous artifacts/docking.snap
```

### Building an Arch package

```bash
# Arch Linux tooling
sudo pacman -S --needed base-devel git python python-pip

# Build package
./packaging/arch/build.sh

# Install locally
sudo pacman -U artifacts/docking-*.pkg.tar.*
```

### Building with Nix

```bash
# Build package output
./packaging/nix/build.sh

# Run from build output
./result-nix/bin/docking
```

## Running

```bash
# Via entry point
docking

# Or directly
python run.py

# With debug logging
DOCKING_LOG_LEVEL=DEBUG python run.py
```

## Configuration

Config is stored at `~/.config/docking/dock.json` (auto-created on first run).

```json
{
  "icon_size": 48,
  "zoom_enabled": true,
  "zoom_percent": 1.5,
  "zoom_range": 3,
  "position": "bottom",
  "monitor_index": -1,
  "autohide": false,
  "hide_delay_ms": 0,
  "unhide_delay_ms": 0,
  "hide_time_ms": 250,
  "previews_enabled": true,
  "lock_icons": false,
  "theme": "default",
  "pinned": ["firefox.desktop", "org.gnome.Nautilus.desktop"]
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `icon_size` | 48 | Base icon size in pixels (all theme proportions scale with this) |
| `zoom_percent` | 1.5 | Max zoom multiplier (1.5 = 150%) |
| `zoom_range` | 3 | Icon widths over which zoom tapers off |
| `position` | bottom | Dock edge: bottom, top, left, right |
| `monitor_index` | -1 | Target monitor index (`-1` = primary monitor, `0..N` = specific monitor) |
| `autohide` | false | Hide dock when cursor leaves |
| `hide_delay_ms` | 0 | Delay before hiding starts (0 = instant) |
| `hide_time_ms` | 250 | Duration of hide/show slide animation |
| `previews_enabled` | true | Show window preview thumbnails on hover |
| `theme` | default | Theme name (loads from `assets/themes/{name}.json`) |
| `pinned` | [] | Desktop file IDs resolved via `$XDG_DATA_DIRS` |

All settings are also configurable via the dock's right-click menu. On multi-monitor setups, use **Display** to move the dock to another monitor.

## Managing Dock Items

- **Drag and drop**: Drag a `.desktop` file from your file manager onto the dock
- **Right-click running app**: "Keep in Dock" to pin
- **Drag off**: Drag an icon upward off the dock to remove (poof animation)
- **Right-click pinned app**: "Remove from Dock" to unpin
- **Edit config**: Add desktop IDs to `"pinned"` in `dock.json`

## Applets

Applets are custom widgets that live in the dock alongside application icons. Enable them via right-click on the dock background -> **Applets**.

### Applet Architecture

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

### Clock

![Clock applet](images/clock.png)

Analog or digital clock face. The analog mode uses SVG layers for a realistic clock face with hour/minute hands.

**Right-click options:**
- **Digital Clock** -- switch between analog and digital display
- **24-Hour Clock** -- toggle 12/24-hour format
- **Show Date** -- show date below time (digital mode only)

**Preferences stored:** `show_digital`, `show_military`, `show_date`

### Trash

![Trash applet](images/trash.png)

Shows the current state of the system trash. Icon switches between empty and full automatically via file monitoring.

**Click:** Open trash folder in file manager
**Right-click options:**
- **Open Trash** -- open in file manager
- **Empty Trash** -- permanently delete all trashed items (uses Caja/Nautilus DBus when available)

### Desktop

![Desktop applet](images/desktop.png)

Toggle "show desktop" mode -- minimizes or restores all windows.

**Click:** Toggle show/hide all windows

### CPU Monitor

![CPU Monitor applet](images/cpu_monitor.png)

Circular gauge showing real-time CPU and memory usage. The fill color shifts from green (idle) to red (busy). A white arc around the edge shows memory usage.

**Tooltip:** `CPU: 23.5% | Mem: 67.2%`

**Update interval:** 1 second (with 3% CPU / 1% memory threshold to avoid excessive redraws)

### Battery

![Battery applet](images/battery.png)

Shows battery charge level using standard FreeDesktop icons. Reads from `/sys/class/power_supply/BAT0/`. Icon changes based on charge level (full, good, low, caution, empty) and charging state.

**Tooltip:** Shows percentage (e.g. "85%") or "No battery"

**Update interval:** 60 seconds

### Brightness

![Brightness applet](images/brightness.png)

Screen brightness control via `xrandr`. Auto-detects the primary display output and tracks live brightness value.

**Click:** Reset brightness to 100%
**Scroll:** Adjust brightness by small steps
**Right-click options:**
- **Show Level** -- toggle percentage text overlay on icon

**Tooltip:** `Brightness: N%`

**Update interval:** 5 seconds

### Weather

![Weather applet](images/weather.png)

Shows current weather and air quality for a selected city with a 5-day forecast. Uses the [Open-Meteo](https://open-meteo.com/) weather and air quality APIs with automatic caching and retry.

**Click:** Open forecast in browser
**Right-click options:**
- **Show Temperature** -- toggle temperature overlay on icon
- **Change City...** -- opens search dialog with autocomplete (48,000 cities)

**Tooltip:** Bold city header + current conditions + air quality + daily forecast with icons:
```
Contagem, Brazil
29°C, Clear sky
Air: Good
Mon: 25/29°C, Partly cloudy
Tue: 28/32°C, Rain
```

**Preferences stored:** `city_display`, `lat`, `lng`, `show_temperature`

**Update interval:** 5 minutes (shared between API cache and polling timer)

### Moon

![Moon applet](images/moon.png)

Moon phase applet with Cairo-rendered moon disc and illumination shading. Fetches phase data asynchronously and falls back gracefully while loading.

**Click:** Refresh moon data now
**Right-click options:**
- **Show Phase Name** -- toggle phase label overlay on icon
- **Refresh** -- force a refresh

**Tooltip:** Multi-line phase summary with illumination percentage and description

**Update interval:** 6 hours

### Clippy

![Clippy applet](images/clippy.png)

Clipboard history manager. Monitors the system clipboard and stores the last 15 text entries.

**Click:** Copy the currently selected clip back to the clipboard
**Scroll:** Cycle through clipboard history (tooltip updates instantly)
**Right-click:** List of all clips (newest first), click to copy. "Clear" to empty history.

**Preferences stored:** `max_entries`

### Color Picker

![Color Picker applet](images/color.png)

Eyedropper color picker. Click enters fullscreen pick mode, samples a pixel color, copies hex value to clipboard, and updates the icon swatch.

**Click:** Start pick mode and sample next clicked pixel
**Right-click options:**
- **Copy #RRGGBB** -- copy current sampled value
- **Show Hex** -- toggle hex label overlay on icon

**Tooltip:** Current sampled hex value

**Preferences stored:** `show_hex`, `r`, `g`, `b`, `hex`

### Applications

![Applications applet](images/applications.png)

Categorized application launcher. Groups all installed `.desktop` applications by FreeDesktop category (Multimedia, Development, Internet, etc.) with icons.

**Right-click:** Categorized submenus with application icons. Click an app to launch it.

### Network

![Network applet](images/network.png)

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

### Bluetooth

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

### Power Profiles

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

Detailed backend/mapping notes: [docs/POWER_PROFILES.md](docs/POWER_PROFILES.md)

### Notifications

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

### Session

![Session applet](images/session.png)

Lock, logout, suspend, restart, or shut down via `loginctl`/`systemctl`.

**Click:** Lock screen
**Right-click options:**
- **Lock Screen** -- `loginctl lock-session`
- **Log Out** -- `loginctl terminate-session`
- **Suspend** -- `systemctl suspend`
- **Restart** -- `systemctl reboot`
- **Shut Down** -- `systemctl poweroff`

### Calendar

![Calendar applet](images/calendar.png)

Shows today's date as a calendar page icon with red header (weekday) and day number.

**Click:** Toggle a GtkCalendar popup
**Tooltip:** Full date (e.g. "Tuesday, February 25")

**Update interval:** 30 seconds (refreshes icon at midnight)

### Workspaces

![Workspaces applet](images/workspace.png)

Workspace switcher with a visual grid icon. Active workspace is highlighted in blue.

**Click:** Cycle to next workspace
**Scroll:** Switch workspace up/down
**Right-click options:** Radio list of all workspaces

**Tooltip:** Active workspace name

### Screenshot

![Screenshot applet](images/screenshot.png)

Capture screenshots via the best available tool. Auto-detects mate-screenshot, gnome-screenshot, xfce4-screenshooter, spectacle, flameshot, or scrot.

**Click:** Full-screen capture
**Right-click options:**
- **Full Screen** -- capture entire screen
- **Window** -- capture active window
- **Region** -- interactive area selection
- **Full Screen in 3s/5s/7s/9s** -- delayed full-screen capture

### Volume

![Volume applet](images/volume.png)

System volume control. Auto-detects pactl (PulseAudio/PipeWire) or amixer (ALSA). Icon switches between muted/low/medium/high based on level.

**Click:** Toggle mute
**Scroll:** Adjust volume ±5%
**Tooltip:** `Volume: 75%` or `Muted`

**Update interval:** 1 second (refreshes only on change)

### Music

Media controller applet with album-art icon rendering. Uses MPRIS over DBus first, then playerctl fallback for controls when needed.

Current support note: tested with **VLC**, **Clementine**, **Amberol**, and **Recordbox**. In general, the applet should work with MPRIS-compatible players (with `playerctl`/backend fallbacks where available).

**Click:** Play/pause
**Scroll:** Player volume ±5%
**Right-click options:**
- **Previous**
- **Play** / **Pause**
- **Next**
- **Volume Up** / **Volume Down**

**Tooltip:** multiline summary, e.g. `Artist - Title`, `Album: ...`, `Vol N%`

### Pomodoro

![Pomodoro applet](images/pomodoro.png)

Pomodoro timer with a flat tomato icon. Auto-cycles through work/break phases with configurable durations. Triggers urgent bounce+glow on phase transitions.

**Click:** Start/pause toggle
**Right-click options:**
- **Reset** -- back to idle
- **Work duration** -- 15/25/30/45 min presets
- **Break duration** -- 5/10 min presets
- **Long break duration** -- 15/20/30 min presets

**Preferences stored:** `work`, `break_`, `long_break`

### Separator

Transparent gap divider between dock items. Supports multiple instances -- each with independent, persistent size.

**Scroll:** Adjust gap width (±2px, range 2–48px)
**Right-click options:**
- **Increase Gap** / **Decrease Gap**
- **Remove from Dock**

Added via right-click on dock background -> **Add Separator** (inserts at click position).

### Hydration

![Hydration applet](images/hydration.png)

Water drop icon that drains over a configurable interval, reminding you to drink water. Click to refill. Triggers urgent bounce when empty.

**Click:** Refill (log a drink)
**Scroll:** No-op
**Right-click options:**
- **Show Timer** -- toggle countdown overlay on icon
- **Interval presets** -- 15/30/45/60/90 min

**Preferences stored:** `interval`, `show_timer`

### Quote

Quote/joke applet inspired by the original Cairo-Dock Quote plugin. Ships with local fallback quotes and supports online refresh from active sources.

**Click:** Show next quote
**Right-click options:**
- **Next Quote**
- **Copy Quote** -- copy current quote to clipboard
- **Refresh from Web**
- **Source** -- switch source (Quotationspage, Qdb, Danstonchat, Viedemerde, Fmylife, Vitadimerda, Chucknorrisfactsfr)

**Preferences stored:** `source`

### Ambient

![Ambient applet](images/ambient.png)

Looping ambient soundscape player. Bundled with 7 CC0/Public Domain nature sounds plus procedural white/pink noise via GStreamer.

**Click:** Toggle play/stop
**Scroll:** Adjust volume ±10%
**Right-click:** Sound selection (Birds, Boat, Coffee Shop, Fireplace, Stream, Summer Night, Wind, White Noise, Pink Noise)

**Preferences stored:** `sound`, `volume`

## Theming

Themes are JSON files in `docking/assets/themes/`. Nine built-in themes are included:

- `default` -- light theme
- `default-dark` -- dark variant
- `matte` -- flat appearance
- `transparent` -- minimal, see-through
- `ubuntu-mate` -- matches Ubuntu MATE panel style
- `yaru-dark` -- matches Yaru dark theme
- `nord` -- cool, desaturated dark
- `gruvbox` -- warm earthy dark
- `solarized` -- soft light Solarized variant

All layout values use a **scaling unit** (tenths of a percent of `icon_size`). This means themes adapt automatically to any icon size.

**Creating a custom theme:** Copy an existing theme JSON and modify the colors and proportions. Place it in the `assets/themes/` directory -- it will appear in the right-click Themes menu.

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

## Pre-commit Hooks

Runs automatically on `git commit`:
- **check-yaml** -- validate YAML files
- **end-of-file-fixer** -- ensure trailing newline
- **trailing-whitespace** -- remove trailing spaces
- **ruff format** -- code formatting
- **ruff check** -- linting (E, W, F, I rules)
- **ty check** -- type checking
- **pytest** -- full test suite

## CI/CD Pipeline

GitHub Actions is split across two workflows:

- **`CI`** (`.github/workflows/ci.yml`)
  - Triggers on push to `master`, PRs to `master`, and `v*` tags.
  - **Quality**: `ruff check`, `ruff format --check`, `ty check`.
  - **Test matrix**:
    - Ubuntu 22.04 / Python 3.10
    - Ubuntu 24.04 / Python 3.12
    - Debian 11 / Python 3.10
    - Debian 12 / Python 3.12
  - **Coverage**: pytest-cov on Ubuntu with `--cov-fail-under=55`, artifacts uploaded (XML/HTML), optional Codecov upload when token is configured.
  - **Packaging artifacts**:
    - `.deb` (with install validation)
    - `.rpm`
    - `.flatpak`
    - `.snap`
    - `.AppImage`
    - Arch package (`.pkg.tar.*`)
    - Nix output tarball + store path
  - **Release step (CD)**:
    - Runs on `master` only after all package builds.
    - Reads version from `pyproject.toml`, checks latest GitHub Release, and only releases if version is newer.
    - Creates/pushes `v<version>` tag (if missing), normalizes artifact names, and publishes a GitHub Release with standardized files.

- **`Security`** (`.github/workflows/security.yml`)
  - Triggers on push/PR to `master` plus weekly schedule (`0 6 * * 1`).
  - Runs:
    - `pip-audit` against runtime dependencies exported from `pyproject.toml`
    - `bandit` SAST scan on `docking/` (excluding tests/packaging)

## Additional Docs

- [Architecture Maintainer Map](docs/ARCHITECTURE.md)
- [Icon Assets and Packaging](docs/ICONS.md)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes (tests required for new features)
4. Run `ruff format docking/ tests/` for formatting
5. Ensure `ruff check && ty check && pytest tests/` passes
6. Submit a pull request

## License

GPL-3.0-or-later
