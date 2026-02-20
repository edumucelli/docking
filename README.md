# Docking

A lightweight dock for Linux, inspired by [Plank](https://github.com/ricotz/plank). Written entirely in Python using GTK 3 and Cairo.

## Features

- Pinned application launchers with click-to-launch
- Running application indicators (dots)
- Parabolic icon zoom on hover (Plank's displacement formula from `PositionManager.vala`)
- 3D shelf background with Plank's Yaru-light theme (gradient fill, inner highlight bevel)
- Auto-hide with cubic easing animation
- Window preview thumbnails on hover (X11 foreign window capture)
- Drag-to-reorder with slide animation
- Drag `.desktop` files from file manager to add icons (with gap insertion effect)
- Drag icons off dock to remove (Plank's poof sprite animation)
- Right-click context menu (pin/unpin, close, auto-hide toggle, quit)
- Theming via JSON
- Window tracking via libwnck (running state, active window, smart focus toggle)
- X11 dock struts (reserves screen space)
- Input shape region (clicks pass through transparent areas)
- Debug logging via `DOCKING_LOG_LEVEL=DEBUG`

## Requirements

- Linux with X11
- Python 3.10+
- System packages (Ubuntu/Debian):

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-wnck-3.0 gir1.2-gdkpixbuf-2.0
```

## Installation

```bash
# Create a venv with access to system GI bindings
uv venv --python /usr/bin/python3 --system-site-packages .venv
source .venv/bin/activate

# Install in editable mode with dev dependencies
uv pip install -e ".[dev]"
```

If using plain `venv` instead of `uv`:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ".[dev]"
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
  "autohide": false,
  "hide_delay_ms": 500,
  "unhide_delay_ms": 0,
  "hide_time_ms": 250,
  "theme": "default",
  "pinned": ["firefox.desktop", "org.gnome.Nautilus.desktop"]
}
```

| Setting | Description |
|---|---|
| `icon_size` | Base icon size in pixels (before zoom) |
| `zoom_percent` | Max zoom multiplier (1.5 = Plank default) |
| `zoom_range` | Icon widths over which zoom tapers off |
| `autohide` | Hide dock when cursor leaves |
| `hide_delay_ms` | Delay before hiding starts |
| `hide_time_ms` | Duration of hide/show animation |
| `theme` | Theme name (loads from `assets/themes/{name}.json`) |
| `pinned` | Desktop file IDs resolved via `$XDG_DATA_DIRS` |

## Theming

Themes are JSON files in `docking/assets/themes/`. The default theme matches Plank's Yaru-light.

```json
{
  "fill_start": [222, 222, 222, 240],
  "fill_end": [247, 247, 247, 240],
  "stroke": [145, 145, 145, 255],
  "inner_stroke": [248, 248, 248, 255],
  "roundness": 5,
  "item_padding": 12
}
```

## Adding/Removing Dock Items

- **Drag & drop**: Drag a `.desktop` file from your file manager onto the dock
- **Right-click running app**: "Keep in Dock" to pin
- **Drag off**: Drag an icon upward off the dock to remove (poof animation)
- **Right-click pinned app**: "Remove from Dock" to unpin
- **Edit config**: Add desktop IDs to `"pinned"` in `dock.json`

## Tests

```bash
pytest tests/ -v
```

87 tests organized by module:

```
tests/
├── core/       test_config, test_theme, test_zoom
├── platform/   test_model, test_launcher
└── ui/         test_autohide, test_dnd
```

## Pre-commit Hooks

Runs automatically on `git commit`:
- **black** — code formatting
- **flake8** — unused imports/variables
- **mypy** — type checking (0 errors)
- **pytest** — 87 tests

## Architecture

```
docking/
├── app.py                  Entry point, GTK main loop
├── log.py                  Logging config (DOCKING_LOG_LEVEL)
├── core/                   Pure logic, no GTK dependency
│   ├── config.py           Config dataclass, load/save
│   ├── theme.py            Theme dataclass, RGBA type, load
│   └── zoom.py             Parabolic zoom math (Plank's formula)
├── platform/               GTK/X11 system integration
│   ├── model.py            DockItem, DockModel
│   ├── launcher.py         .desktop resolution, icon loading
│   ├── window_tracker.py   Wnck running app detection
│   └── struts.py           X11 _NET_WM_STRUT via ctypes
├── ui/                     GTK rendering & interaction
│   ├── dock_window.py      Main window, events, positioning
│   ├── renderer.py         Cairo drawing (3D shelf, icons, indicators)
│   ├── preview.py          Window thumbnail popup
│   ├── menu.py             Right-click context menus
│   ├── dnd.py              Drag-and-drop (reorder, add, remove)
│   ├── poof.py             Poof smoke animation (Plank's sprite sheet)
│   └── autohide.py         Hide state machine with easing
└── assets/
    ├── poof.svg            Plank's poof sprite sheet
    └── themes/default.json Yaru-light theme
```
