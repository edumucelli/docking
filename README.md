# Docking

A lightweight dock for Linux, inspired by [Plank](https://github.com/ricotz/plank). Written entirely in Python using GTK 3 and Cairo.

## Features

- Pinned application launchers with click-to-launch
- Running application indicators (dots)
- Parabolic icon zoom on hover (Plank's magnification formula)
- Auto-hide with cubic easing animation
- Drag-to-reorder pinned items
- Right-click context menu (pin/unpin, close, preferences)
- Theming via JSON
- Window tracking via libwnck (running state, active window, focus toggle)
- X11 dock struts (reserves screen space)

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

# Install in editable mode
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
```

## Configuration

Config is stored at `~/.config/docking/dock.json` (auto-created on first run).

```json
{
  "icon_size": 48,
  "zoom_enabled": true,
  "zoom_percent": 2.0,
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

Pinned items are `.desktop` file IDs resolved via `$XDG_DATA_DIRS`.

## Theming

Themes live in `themes/` as JSON files. See `themes/default.json` for the schema. Set `"theme": "mytheme"` in config to use `themes/mytheme.json`.

## Tests

```bash
pytest tests/ -v
```

## Architecture

| Module | Purpose |
|---|---|
| `app.py` | Entry point, GTK main loop |
| `dock_window.py` | GTK window, X11 dock hints, struts, event dispatch |
| `dock_renderer.py` | Cairo rendering — background, icons, indicators |
| `dock_model.py` | Data model — pinned + running items |
| `zoom.py` | Parabolic zoom math |
| `autohide.py` | Hide state machine with easing |
| `window_tracker.py` | libwnck running app detection |
| `launcher.py` | XDG .desktop resolution, icon loading |
| `dnd.py` | Drag-to-reorder |
| `menu.py` | Right-click context menus |
| `config.py` | JSON config management |
| `theme.py` | Theme loading |
