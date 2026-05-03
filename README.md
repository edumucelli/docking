# Docking

[![CI](https://github.com/edumucelli/docking/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/edumucelli/docking/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/edumucelli/docking?display_name=tag)](https://github.com/edumucelli/docking/releases)
[![Coverage](https://codecov.io/gh/edumucelli/docking/branch/master/graph/badge.svg)](https://codecov.io/gh/edumucelli/docking)
[![Last commit](https://img.shields.io/github/last-commit/edumucelli/docking)](https://github.com/edumucelli/docking/commits/master)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)

<img src="images/docking.png" alt="Docking" height="48" style="display:block; margin:0 auto;">

A lightweight, feature-rich dock for Linux written in Python with GTK 3 and Cairo. Inspired by [Plank](https://launchpad.net/plank) and [Cairo-Dock](https://github.com/Cairo-Dock), with an extensible applet system for custom widgets.

![all.gif](images/all.gif)

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Configuration](#configuration)
- [Managing Dock Items](#managing-dock-items)
- [Applets](#applets)
- [Theming](#theming)
- [Writing Custom Applets](#writing-custom-applets)
- [Translations](#translations)
- [Developer Workflow](#developer-workflow)
- [Additional Docs](#additional-docs)
- [Contributing](#contributing)
- [License](#license)

## Features

Docking is built around a few core capabilities:

- Fast launcher workflow with running-state indicators and preview interactions.
- Flexible layout with multi-position, multi-monitor, auto-hide, and drag-and-drop organization.
- Broad customization through themes, transparency, icon sizing, menu options, and tooltip controls.
- Native support for pinned files/folders, including left-click folder stacks.
- Extensible applet surface for system status, productivity, media, and utilities.

Highlights:
- 50 built-in applets enabled from the dock menu, including system monitors, productivity tools, launchers, and dock utilities.
- 13 built-in themes with scalable layout values.
- Desktop-environment integration across MATE, Xfce, KDE, Cinnamon, GNOME, and others.
- Unity LauncherEntry support for per-app badge counts and progress bars on dock icons.
- Exports `_DOCKING_BACKGROUND_BLUR_REGION` on X11 so compositors and scripts can read the exact visible shelf rectangle.
- 74 locale catalogs plus English fallback.

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

Download the latest package for your distribution from [GitHub Releases](https://github.com/edumucelli/docking/releases).

Quick options:

- **AppImage**: download, mark executable, and run directly.
- **Debian/Ubuntu**: install the `.deb` package.
- **Fedora/openSUSE-style systems**: install the `.rpm` package.
- **Flatpak, Snap, Arch, and Nix**: release artifacts are published for both x64 and arm64 where supported.

Examples:

```bash
# AppImage
chmod +x docking-latest-linux-x86_64.AppImage
./docking-latest-linux-x86_64.AppImage

# Debian / Ubuntu
sudo apt install ./docking-latest-linux-x86_64.deb

# RPM
sudo dnf install ./docking-latest-linux-x86_64.rpm
```

For development from source:

```bash
git clone https://github.com/edumucelli/docking.git
cd docking
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python /usr/bin/python3 --system-site-packages .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
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

Most settings are available from right-click -> **Preferences**. The config file is stored at:

```text
~/.config/docking/dock.json
```

First run creates a starter dock with Applications, common launchers detected on the system, and a few useful applets such as Clock, Calendar, Weather, System Monitor, Hydration, Notifications, and Session.

Common settings include:

| Setting | What it controls |
| --- | --- |
| Icon size and zoom | Base dock size and hover magnification |
| Position and monitor | Dock edge, primary/specific monitor, or active display behavior |
| Hide behavior | Always visible, autohide, intelligent hide, or window dodge modes |
| Theme and transparency | Built-in theme selection and opacity |
| Mouse actions | Running-app left click and middle click behavior |
| Tooltips and previews | Hover labels and window preview thumbnails |
| Applets and pinned items | Ordered dock contents plus per-item preferences |
| Update checks | Automatic release checks and check interval |

Advanced users can edit `dock.json` directly. Pinned entries support apps, applets, files, and folders.

Update-check preferences are stored in `dock.json`. Runtime update state, such as the last checked timestamp, ignored release version, and remind-later timestamp, is stored under:

```text
~/.local/state/docking/updates.json
```

## Managing Dock Items

- **Drag and drop**: Drag a `.desktop` file, an application, a folder, or a file from your file manager onto the dock
- **Right-click running app**: "Keep in Dock" to pin
- **Drag off**: Drag an icon upward off the dock to remove (poof animation)
- **Right-click pinned app**: "Remove from Dock" to unpin
- **Edit config**: Add desktop IDs to `"pinned"` in `dock.json`

## Applets

Applets are dock widgets for status, launchers, productivity, media, and small utilities. Enable them from right-click on the dock background -> **Applets**.

For a more detailed user-facing applet reference, see [docs/APPLETS.md](docs/APPLETS.md).

| Applet | Summary |
| --- | --- |
| AI Usage | Tracks Claude Code, Codex CLI, and OpenCode usage from the dock. |
| Applications | Opens a searchable categorized launcher for installed desktop apps. |
| Astronomy Picture of the Day | Shows NASA's APOD thumbnail and opens the daily page. |
| Ambient | Plays bundled ambient soundscapes and noise loops. |
| Battery | Shows battery charge, charging state, and power settings access. |
| Bluetooth | Controls Bluetooth power, adapters, paired devices, and discovery. |
| Bookmarks | Opens and manages saved web bookmarks. |
| Brightness | Adjusts screen brightness and can show the current level. |
| Calculator | Opens a small calculator for quick arithmetic. |
| Calendar | Shows today's date and opens a calendar popup. |
| Cam Shield | Shows camera activity and can lock camera access. |
| Caps Lock | Shows Caps Lock and Num Lock state for keyboards without LEDs. |
| Cert Watch | Monitors certificate expiry for saved domains. |
| Clippy | Keeps a short clipboard history and lets you restore previous clips. |
| Clock | Shows analog or digital time with optional date, seconds, and alarm. |
| Color Picker | Samples a screen color and copies the hex value. |
| Currency FX | Tracks selected currency pairs with a compact sparkline. |
| Desk Presence | Tracks active desk time versus away time. |
| Desktop | Toggles show-desktop mode. |
| Drag Share | Uploads dropped files and copies the temporary share URL. |
| Hacker News | Shows and opens Hacker News headlines. |
| Hydration | Reminds you to drink water on a configurable interval. |
| Keyboard Layout | Shows and switches the active keyboard layout. |
| Mic Shield | Shows microphone activity and toggles microphone mute. |
| Moon | Shows moon phase and illumination. |
| Music | Controls the active media player. |
| Network | Shows connection state, signal, IP, and transfer speed. |
| Notifications | Toggles Do Not Disturb and shows pending notification state. |
| Pet | Shows an animated companion that reacts to system activity. |
| Pomodoro | Runs configurable work/break timers. |
| Power Profiles | Switches between power saver, balanced, and performance profiles. |
| Quick Note | Stores and edits one quick note. |
| Quote | Shows local or online quotes and jokes. |
| Random Trivia | Shows trivia questions with answer choices. |
| Recent Files | Opens recently used files. |
| Screenshot | Runs full-screen, window, region, or delayed screenshots. |
| Separator | Adds a persistent adjustable gap between dock items. |
| Session | Locks, logs out, suspends, restarts, or shuts down. |
| Speedtest | Runs an on-demand internet speed test. |
| Stretch Coach | Reminds you to take micro-breaks with optional stretch cards. |
| System Monitor | Shows CPU, memory, disk, and temperature status. |
| Thermals | Shows the hottest sensor and fastest fan from lm-sensors. |
| Today in History | Shows notable events for the current date. |
| Trash | Opens and empties the system trash. |
| Unit Converter | Converts units and currencies from a compact dialog. |
| URL Shortener | Shortens URLs and copies the result. |
| Volume | Controls system volume and mute state. |
| Weather | Shows current weather, air quality, and forecast for saved cities. |
| Window Killer | Click a window to force-close it. |
| Workspaces | Switches between desktop workspaces. |

## Theming

Themes are JSON files in `docking/assets/themes/`. Thirteen built-in themes are included:

- `default` -- light theme
- `onyx` -- dark variant
- `slate` -- flat appearance
- `transparent` -- minimal, see-through
- `olive` -- rounded olive-green theme
- `ember` -- warm dark theme
- `nord` -- cool, desaturated dark
- `glass` -- translucent macOS-style floating pill
- `pill` -- dark floating pill with fully rounded borders
- `paper` -- matte warm floating pill
- `candy` -- playful pastel floating pill
- `gruvbox` -- warm earthy dark
- `solarized` -- soft light Solarized variant

Theme examples:

| Theme | Preview |
| --- | --- |
| Glass | ![Glass theme](images/glass.png) |
| Transparent | ![Transparent theme](images/transparent.png) |

All layout values use a **scaling unit** (tenths of a percent of `icon_size`). This means themes adapt automatically to any icon size.

Theme layout also controls edge spacing through `distance_from_edge`, which is how floating themes such as `slate` keep the dock visually separated from the screen edge.

**Creating a custom theme:** Copy an existing theme JSON and modify the colors and proportions. Place it in the `assets/themes/` directory -- it will appear in the right-click Themes menu.

## Writing Custom Applets

Applets are Python packages under `docking/applets/`. Each applet declares metadata in `__init__.py` and keeps runtime behavior in `applet.py`; larger applets usually split pure logic into `state.py` and icon drawing into `render.py`.

Minimal shape:

```text
docking/applets/myapplet/
  __init__.py   # AppletMeta declaration
  applet.py     # GTK lifecycle, click/scroll/menu behavior
  state.py      # optional pure logic
  render.py     # optional icon rendering
```

For architecture details, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Translations

Docking ships gettext catalogs for 74 locales plus English fallback. The app follows the user's system locale automatically.

Common commands:

```bash
./tools/i18n.sh --extract
./tools/i18n.sh --check-pot-sync
./tools/i18n.sh --check-catalogs --allow-incomplete
./tools/i18n.sh --compile
```

Feature branches only need to keep `docking/locale/docking.pot` in sync with source strings. Full `.po` catalog refreshes are handled in translation-only pull requests with:

```bash
./tools/i18n.sh --update-translations
```

## Developer Workflow

Useful local checks:

```bash
.venv/bin/ruff format docking/ tests/
.venv/bin/ruff check docking/ tests/
.venv/bin/ty check docking/
.venv/bin/python -m pytest tests/ -q
```

For GUI/integration tests under a headless X11 session:

```bash
bash tools/test_gui_headless.sh
```

Install or refresh the local commit hook with:

```bash
./tools/install_precommit_hook.sh
```

Packaging scripts live under `packaging/` for AppImage, Arch, Debian, Flatpak, Nix, PPA, RPM, and Snap builds.

Docking also exposes a session-bus API for item inspection and control. See [docs/DBUS.md](docs/DBUS.md).

## Additional Docs

- [Applet Reference](docs/APPLETS.md)
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
