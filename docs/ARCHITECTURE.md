# Docking Architecture

Maintainer map for the `docking` codebase: module ownership, runtime call graph, and extension points.

## Scope

- App type: GTK3/X11 dock with pluggable applets.
- Entrypoint: `docking/app.py` (`docking` console script and `run.py` both route here).
- Main constraints: X11/`libwnck`, GI bindings, Cairo rendering, low-latency pointer interaction.

## Top-Level Responsibility Map

- `docking/app.py`: composition root; wires config, model, renderer, window, controllers, and starts GTK main loop.
- `docking/log.py`: logger config (`DOCKING_LOG_LEVEL`).
- `docking/core/`: pure logic and immutable-ish domain types.
- `docking/platform/`: OS/window-system integration.
- `docking/ui/`: input handling, rendering, window geometry, animation orchestration.
- `docking/applets/`: plugin system + built-in applets.
- `docking/assets/`: themes, icons, sounds, weather city DB, poof sprite.
- `tests/`: behavior contracts (math, state transitions, rendering structure, applet logic).

## Core Layer (`docking/core`)

- `config.py`: persisted dock settings at `~/.config/docking/dock.json`.
- `position.py`: edge enum (`bottom/top/left/right`) + orientation helper.
- `theme.py`: loads JSON themes and converts scale units to pixels.
- `zoom.py`: parabolic zoom/displacement layout math used by renderer and hit-testing.

Design intent:
- Keep logic GTK-free where possible for fast tests and deterministic behavior.

## Platform Layer (`docking/platform`)

- `model.py`: source of truth for visible dock items.
- Owns pinned items, transient running items, applet lifecycle, and animation timestamps.
- `launcher.py`: `.desktop` resolution, icon loading cache, desktop actions, launch helpers.
- `window_tracker.py`: maps Wnck windows to desktop IDs, running/active/urgent state updates.
- `struts.py`: X11 `_NET_WM_STRUT_PARTIAL` management via ctypes/Xlib.

Design intent:
- Isolate system integration details from UI drawing and pure math.

## UI Layer (`docking/ui`)

- `dock_window.py`: main dock window; event wiring, cursor state, input region, positioning/struts coordination.
- `renderer.py`: Cairo draw pipeline and animation rendering (shelf, icons, indicators, glows).
- `autohide.py`: hide/show state machine and easing.
- `hover.py`: hover tracking, preview timer, animation pump.
- `tooltip.py`: custom tooltip popup placement/caching.
- `preview.py`: window thumbnail popup and activation.
- `menu.py`: context menus for item and dock background actions.
- `dnd.py`: internal reorder + external `.desktop` drops + drag-off removal poof.
- `effects.py`, `shelf.py`, `poof.py`: rendering helpers/assets-specific effects.

Design intent:
- Keep all event choreography in one place (`DockWindow`), delegate focused behavior to helpers.

## Applet Layer (`docking/applets`)

- Base API: `applets/base.py` (`Applet` abstract class).
- Registry: `applets/__init__.py` (`get_registry()`).
- Built-ins (18): ambient, applications, battery, calendar, clippy, clock, cpumonitor, desktop, hydration, network, pomodoro, screenshot, separator, session, trash, volume, weather, workspaces.
- Weather is a subpackage (`applets/weather/*`) with API client and city lookup DB access.

Design intent:
- Each applet owns its icon rendering, timers/signals, menu, and persisted prefs.

## Startup Call Graph

```text
docking (console script) / run.py
  -> docking.app:main()
     -> Config.load()
     -> Theme.load()
     -> Launcher()
     -> DockModel(config, launcher)
     -> DockRenderer()
     -> WindowTracker(model, launcher)
     -> DockWindow(config, model, renderer, theme, tracker)
     -> AutoHideController(window, config)
     -> DnDHandler(window, model, config, renderer, theme, launcher)
     -> MenuHandler(window, model, config, tracker, launcher)
     -> PreviewPopup(tracker)
     -> model.start_applets()
     -> window.show_all()
     -> Gtk.main()
```

## Runtime Interaction Call Graph

```text
Pointer motion
  DockWindow._on_motion
    -> HoverManager.update
       -> compute_layout(...)
       -> hit_test(...)
       -> TooltipManager.update(...)
    -> queue_draw
    -> DockRenderer.draw(...)

Click release
  DockWindow._on_button_release
    -> hit_test
    -> if applet: Applet.on_clicked()
    -> else launch() or WindowTracker.toggle_focus()
    -> set animation timestamps
    -> HoverManager.start_anim_pump()

Window changes (open/close/active/urgent)
  WindowTracker signals
    -> _update_running()
    -> DockModel.update_running(...)
    -> DockWindow._on_model_changed()
    -> queue_draw
```

## Data Ownership and State Boundaries

- `Config`: persisted user settings and applet prefs.
- `DockModel`: authoritative visible-item order and per-item runtime state.
- `DockWindow`: current pointer coordinates and input-region shape.
- `AutoHideController`: hide/show state (`VISIBLE/HIDING/HIDDEN/SHOWING`) and progress.
- `DockRenderer`: visual transient state (`slide_offsets`, hover lighten accumulators, smoothed shelf width).

## Extension Points

### Add a New Applet

- Implement subclass of `Applet` in `docking/applets/<name>.py`.
- Set `id`, `name`, `icon_name`, and implement at minimum `create_icon`.
- Optionally implement `on_clicked`, `on_scroll`, `get_menu_items`, `start`, `stop`.
- Register class in `docking/applets/__init__.py`.
- Add tests in `tests/applets/test_<name>.py`.

### Add/Adjust Theme

- Create/edit JSON in `docking/assets/themes/`.
- Layout keys use scaled units interpreted by `Theme.load`.
- Animation keys are direct values (ms/fractions/opacity).

### Change Dock Behavior

- Input/event policy: `docking/ui/dock_window.py`.
- Autohide timing/easing: `docking/ui/autohide.py`.
- Rendered effects/layout visuals: `docking/ui/renderer.py` and helpers.
- Right-click menu actions: `docking/ui/menu.py`.
- DnD and pin/reorder semantics: `docking/ui/dnd.py` + `platform/model.py`.

### Change Platform Integration

- `.desktop` resolution/actions/launching: `docking/platform/launcher.py`.
- Running window matching policy: `docking/platform/window_tracker.py`.
- Strut math and X11 property writes: `docking/platform/struts.py`.

## Testing Map

- `tests/core/`: pure math/config/theme contracts.
- `tests/platform/`: model and integration math (launcher/struts/window mapping).
- `tests/ui/`: geometry/state machine/renderer structure/regression behaviors.
- `tests/applets/`: applet parsing/state/menu/rendering/prefs.

Useful invariant examples enforced by tests:
- Zoom/rest bounds contracts in `core.zoom`.
- Input region two-state model (content rect vs hidden trigger strip).
- Offscreen + atomic blit renderer pattern (anti-flicker regression guard).
- Model transition correctness for running/urgent/pin/reorder.

## Packaging and Delivery Notes

- Python project metadata in `pyproject.toml`; Debian packaging under `packaging/deb/`.
- Debian build vendors pip deps into `/usr/lib/docking/vendor` and prepends that path at runtime in `app.py`.
- CI workflow in `.github/workflows/ci.yml` runs lint/format/type/tests and `.deb` build.

## Maintainer Checklist (When Changing Behavior)

- Update tests first or in lockstep, especially for UI geometry/state transitions.
- Keep pure logic in `core/` where feasible.
- Avoid coupling applet internals into renderer/window; use `DockItem` fields and applet API.
- For pointer or autohide changes, validate interaction among:
  - input region updates,
  - hover/tooltip/preview crossing events,
  - hide/show state transitions,
  - DnD motion/leave behavior.
