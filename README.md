# Docking

A lightweight dock for Linux, written entirely in Python using GTK 3 and Cairo.

## Features

**Core dock:**
- Pinned application launchers with click-to-launch
- Running application indicators (dots) with active window glow
- Parabolic icon zoom on hover with per-icon displacement
- 3D shelf background (gradient fill, inner highlight bevel)
- Tooltip showing app name above hovered icon
- Smart focus: click running app to focus/minimize, Ctrl+click to launch new instance

**Visual effects:**
- Hover lighten — additive brightness fade on hovered icon
- Click darken — brief sine-curve pulse on click
- Launch bounce — two-bounce momentum decay animation when launching
- Urgent bounce — single bounce when app demands attention
- Active window glow — color-matched gradient using icon's dominant color
- Cascade hide — shelf slides faster than icons for layered effect
- Smooth zoom decay — icons shrink while dock slides away (no snap)

**Window previews:**
- Thumbnails of running windows on hover (X11 foreign window capture)
- Click thumbnail to activate specific window
- Dock stays visible while browsing previews
- Toggleable via right-click menu

**Drag and drop:**
- Drag to reorder icons (with slide animation)
- Drag `.desktop` files from file manager to add icons (gap insertion effect)
- Drag icons off dock to remove (poof smoke animation)

**Auto-hide:**
- Cubic easing animation (ease-in hide, ease-out show)
- 0ms default delay (instant, matches Plank)
- 2px trigger strip at screen edge when hidden
- Struts update instantly on toggle (windows resize immediately)

**Other:**
- Right-click context menu (pin/unpin, close, auto-hide, previews, quit)
- X11 dock struts (reserves screen space for icons when not auto-hiding)
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
  "hide_delay_ms": 0,
  "unhide_delay_ms": 0,
  "hide_time_ms": 250,
  "previews_enabled": true,
  "theme": "default",
  "pinned": ["firefox.desktop", "org.gnome.Nautilus.desktop"]
}
```

| Setting | Description |
|---|---|
| `icon_size` | Base icon size in pixels — all theme proportions scale with this |
| `zoom_percent` | Max zoom multiplier (1.5 = 150%) |
| `zoom_range` | Icon widths over which zoom tapers off |
| `autohide` | Hide dock when cursor leaves |
| `hide_delay_ms` | Delay before hiding starts (0 = instant) |
| `hide_time_ms` | Duration of hide/show slide animation |
| `previews_enabled` | Show window preview thumbnails on hover |
| `theme` | Theme name (loads from `assets/themes/{name}.json`) |
| `pinned` | Desktop file IDs resolved via `$XDG_DATA_DIRS` |

## Theming

Themes are JSON files in `docking/assets/themes/`. All layout values use a **scaling unit** — tenths of a percent of `icon_size`. At load time, values are multiplied by `icon_size / 10.0` to get pixels. This means themes work at any icon size — proportions adapt automatically.

```json
{
  "fill_start": [222, 222, 222, 240],
  "fill_end": [247, 247, 247, 240],
  "stroke": [145, 145, 145, 255],
  "stroke_width": 1.0,
  "inner_stroke": [248, 248, 248, 255],
  "roundness": 5,
  "indicator_color": [80, 80, 80, 200],
  "active_indicator_color": [50, 50, 50, 255],
  "indicator_size": 5,
  "h_padding": 0,
  "top_padding": -7,
  "bottom_padding": 1,
  "item_padding": 2.5,
  "urgent_bounce_height": 1.66,
  "launch_bounce_height": 0.625,
  "urgent_bounce_time_ms": 600,
  "launch_bounce_time_ms": 600,
  "click_time_ms": 300,
  "hover_lighten": 0.2,
  "active_time_ms": 150,
  "max_indicator_dots": 3,
  "glow_opacity": 0.6
}
```

**Scaling example at icon_size=48:** `scaled = 48/10 = 4.8`, so `item_padding: 2.5` → `2.5 × 4.8 = 12px`. Negative `top_padding` makes icons overflow above the shelf.

**Shelf height** is derived automatically: `max(0, icon_size + top_offset + bottom_offset)`. With the default theme at 48px: `48 + (-31.6) + 4.8 ≈ 21px`.

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

150 tests organized by module:

```
tests/
├── core/       test_config, test_theme, test_zoom
├── platform/   test_model, test_launcher
└── ui/         test_autohide, test_dnd, test_effects, test_hover,
                test_renderer, test_shelf, test_tooltip
```

## Pre-commit Hooks

Runs automatically on `git commit`:
- **black** — code formatting
- **flake8** — unused imports/variables
- **mypy** — type checking (0 errors)
- **pytest** — 150 tests

## Architecture

```
docking/
├── app.py                  Entry point, GTK main loop
├── log.py                  Logging config (DOCKING_LOG_LEVEL)
├── core/                   Pure logic, no GTK dependency
│   ├── config.py           Config dataclass, load/save
│   ├── theme.py            Theme with scaling units, RGB/RGBA types
│   └── zoom.py             Parabolic zoom math, displacement formula
├── platform/               GTK/X11 system integration
│   ├── model.py            DockItem, DockModel
│   ├── launcher.py         .desktop resolution, icon loading
│   ├── window_tracker.py   Wnck running app detection
│   └── struts.py           X11 _NET_WM_STRUT via ctypes
├── ui/                     GTK rendering & interaction
│   ├── dock_window.py      Main window, events, coordinate conversion
│   ├── renderer.py         Draw orchestration, icons, indicators, glow
│   ├── shelf.py            Shelf background drawing (3D bevel)
│   ├── effects.py          Easing bounce, icon color extraction
│   ├── tooltip.py          App name tooltip (Cairo-drawn)
│   ├── hover.py            Hover tracking, preview timer, anim pump
│   ├── preview.py          Window thumbnail popup
│   ├── menu.py             Right-click context menus
│   ├── dnd.py              Drag-and-drop (reorder, add, remove)
│   ├── poof.py             Poof smoke animation (sprite sheet)
│   └── autohide.py         Hide state machine with easing
└── assets/
    ├── poof.svg            Poof sprite sheet
    └── themes/default.json Yaru-light inspired theme
```
