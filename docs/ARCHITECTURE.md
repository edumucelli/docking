# Docking Architecture

This document describes the current architecture of `docking`. It replaces the
older overview that predated the geometry refactor, the UI assembly split, and
the first wave of `DockWindow` decomposition.

This file focuses on what is true in production code today.

## Scope

- App type: GTK3/X11 dock with pluggable applets
- Entrypoint: `docking/app.py`
- UI assembly root: `docking/ui/factory.py`
- Main runtime constraints:
  - X11/`libwnck` integration
  - GI bindings
  - Cairo rendering
  - low-latency pointer interaction
  - geometry/input-mask correctness during autohide motion

## Architectural Shape

The dock is now organized around a clearer split than it had originally:

- `docking/app.py`
  Bootstraps process-wide concerns and creates the top-level object graph.
- `docking/core/`
  Configuration, theme, position, zoom, and other mostly GTK-free logic.
- `docking/platform/`
  Window tracking, launch integration, barriers, struts, and environment tweaks.
- `docking/ui/`
  Runtime shell, geometry, rendering, interaction policy, placement, autohide,
  hover, menus, previews, drag-and-drop, and focused controllers.
- `docking/applets/`
  Plugin-style built-in applets and their internal submodules.

The most important structural changes since the older architecture document are:

- shared geometry is now a first-class module with an explicit frame type
- UI assembly moved into `docking/ui/factory.py`
- `DockWindow` is no longer expected to own every dock concern directly
- narrow runtime surfaces exist for handlers that should not depend on the
  entire window object graph

## Startup and Assembly

The startup path is now:

```text
docking / run.py
  -> docking.app:main()
     -> apply_tweaks(detect_desktop())
     -> Config.load()
     -> Theme.load(...).with_opacity(config.transparency)
     -> Launcher()
     -> DockModel(config, launcher)
     -> DockRenderer()
     -> WindowTracker(model, launcher, config)
     -> build_dock_window(...)
        -> DockWindow(...)
        -> WindowDodgeMonitor(...)
     -> DockItemsService(model, window)
     -> window.show_all()
     -> GLib.idle_add(_start_runtime, items_service, model)
        -> items_service.start()
        -> model.start_applets()
     -> Gtk.main()
     -> items_service.stop()
     -> model.stop_applets()
```

`build_dock_window()` is still the assembly boundary between app bootstrap and
the GTK shell, but it is thinner than it was during the first round of UI
splitting. `DockWindow` now constructs its own core UI collaborators directly,
while `factory.py` mainly wires the shell to the platform-facing dodge monitor.

## Top-Level Ownership Map

### `docking/app.py`

Owns:

- process bootstrap
- GI setup
- vendor path setup for packaged installs
- config/theme/model/renderer/tracker creation
- GTK main-loop lifecycle

Does not own:

- detailed UI graph wiring
- geometry logic
- autohide policy

### `docking/ui/factory.py`

Owns:

- top-level shell bootstrap around `DockWindow`
- wiring of the platform-facing `WindowDodgeMonitor`
- the boundary between app bootstrap and a fully usable dock window

This module is intentionally thin now. The broader late-attachment phase and
`DockComponents` model described in older docs no longer exist as the main UI
assembly strategy; most UI collaborators are created inside `DockWindow`
itself.

## Core Layer

Primary modules:

- `docking/core/config.py`
- `docking/core/theme.py`
- `docking/core/position.py`
- `docking/core/zoom.py`
- `docking/core/items.py`

Responsibilities:

- persisted user configuration and applet prefs
- first-run starter-dock seeding
- crash-safe config persistence with atomic replace and backup fallback
- theme loading, scaling, and opacity adjustment
- dock-edge/orientation helpers
- zoom/displacement math
- shared item-level domain constants and data shapes

Design rule:

- keep deterministic logic GTK-free where practical
- make math and data contracts easy to test in isolation

## Platform Layer

Primary modules:

- `docking/platform/model.py`
- `docking/platform/window_tracker.py`
- `docking/platform/launcher.py`
- `docking/platform/struts.py`
- `docking/platform/barriers.py`
- `docking/platform/environment.py`

Responsibilities:

- authoritative visible dock item list and applet lifecycle
- running/active/urgent window tracking through Wnck
- desktop-file resolution, launch helpers, icon lookup, and URL opening
- X11 strut writes and clearing
- X11 blur-region hint export for the visible shelf rect
- pointer barrier integration
- desktop-environment-specific tweaks at startup

Design rule:

- isolate OS/window-system details from renderer and UI policy

## UI Layer

The UI layer is now much more intentionally split than it used to be.

### `docking/ui/dock_window.py`

Current role:

- GTK/X11 shell
- drawing-area event adapter
- current pointer position storage
- input shape application
- runtime coordination between collaborators

`DockWindow` is no longer the whole UI architecture. It is the shell that
routes events and keeps the main runtime collaborators together.

Important owned state:

- `cursor_x` / `cursor_y`
- `dock_hovered`
- `_current_geometry_frame`
- `_applied_input_frame`

Important collaborators:

- `DockGeometryBuilder`
- `DockPlacementController`
- `DockInteractionCoordinator`
- `HoverManager`
- `TooltipManager`
- `AutoHideController`
- `DnDHandler`
- `MenuHandler`
- `PreviewPopup`

### `docking/ui/geometry.py`

This is now one of the core architectural modules.

Key types:

- `Rect`
- `ItemGeometry`
- `DockGeometryFrame`
- `DockGeometryInputs`
- `DockGeometryBuilder`

Responsibilities:

- build one explicit geometry snapshot for the current dock state
- define dock-wide interaction geometry via `cursor_rect`
- define per-item `draw_rect`, `hover_rect`, `hit_rect`, and related regions
- provide popup anchor geometry
- keep input masking, hover, rendering, and hit-testing aligned

Important invariant:

- geometry containment uses normalized half-open bounds

This shared frame model is the main reason edge behavior, hover targeting, and
popup anchoring are more coherent than in the earlier codebase.

### `docking/ui/renderer.py`

Responsibilities:

- Cairo draw pipeline
- shelf/background/icon rendering
- glow, urgent, click, and launch visual effects
- consuming a provided `DockGeometryFrame`

Important rule:

- the renderer should consume geometry, not invent a second layout model

### `docking/ui/autohide.py`

Responsibilities:

- the four-state autohide state machine:
  - `VISIBLE`
  - `HIDING`
  - `HIDDEN`
  - `SHOWING`
- hide/show delays
- animation progress
- `hide_offset`
- `zoom_progress`
- hover/disabled reconciliation into hide/show intent

Recent important behavior:

- hide/show reversal continuity is preserved through inverse easing
- if `_start_showing()` is called while `hide_offset <= 0.0`, the controller
  now snaps directly to `VISIBLE` instead of starting a bogus zero-offset show
  animation

That last fix is the production fix for the autohide "jump out" bug that was
still present in older builds.

### `docking/ui/interaction.py`

Responsibilities today:

- effective enter handling
- effective leave handling
- menu popup open/close policy
- pointer-inside-current-input-rect checks
- preview-aware leave policy
- cursor-preservation policy during hide

Important current status:

- this module is a real policy layer now

Current behavior is still event-led with geometry confirmation:

- `DockWindow` receives raw crossing/motion events
- interaction filters them through the current input frame
- effective leave/enter is then applied

That is an improvement over raw widget-boundary behavior, but it is still not
the final containment-led model planned in the parity doc.

### `docking/ui/placement.py`

Responsibilities:

- monitor selection
- workarea/monitor-edge placement
- deferred reposition scheduling
- X11 struts
- pointer barriers
- active-display polling

This module exists because placement concerns are platform-facing and should
not be mixed into hover or renderer logic.

### `docking/ui/hover.py`

Responsibilities:

- hovered-item tracking from shared geometry
- tooltip refresh coordination
- preview show timer lifecycle
- short-lived animation pump for click/launch/urgent redraws

Important current behavior:

- hover uses geometry-provided hover regions
- tooltip display is suppressed while autohide is in `SHOWING` so tooltips do
  not visibly chase a moving dock

### `docking/ui/tooltip.py`

Responsibilities:

- tooltip popup lifecycle
- stable tooltip placement from shared geometry
- text caching/update rules

### `docking/ui/preview.py`

Responsibilities:

- window-thumbnail popup lifecycle
- preview show/hide timers
- activation of selected windows
- cooperation with autohide through explicit preview-aware policy

Current production model:

- leaving the dock while preview is visible schedules preview hide first
- preview disappearance is what may eventually release autohide
- this keeps the dock/preview interaction reachable

### `docking/ui/menu.py`

Responsibilities:

- dock background menu
- item context menus
- settings/about/support integration
- monitor-selection actions
- runtime calls through `DockRuntime`

### `docking/ui/dnd.py`

Responsibilities:

- internal reorder drag-and-drop
- external desktop-file/file drops
- drag-off removal behavior
- autohide disable/re-enable coordination during drag

### `docking/ui/runtime.py`

This is another important architectural improvement since the older document.

Current runtime surfaces:

- `DockRuntime`
- `DockDragRuntime`

Purpose:

- expose narrow imperative APIs to handlers/controllers
- stop passing the full `DockWindow` object to every subsystem

Examples:

- menu handlers ask runtime to open/close popup state, reposition, or hide UI
- drag handlers ask runtime about pointer position, window bounds, and autohide
  transitions without owning the full shell

## Runtime Data Flow

The key runtime loop is now:

```text
raw GTK event
  -> DockWindow stores cursor/button context
  -> DockWindow builds current geometry frame
  -> interaction / hover / DnD / menu consume that shared geometry
  -> renderer draws from the same frame
  -> input mask is refreshed from the same frame if needed
```

That shared-frame rule matters more than any single module split. It is the
main protection against the old class of bugs where rendering, hover, and input
all had slightly different ideas of where the dock really was.

## State Ownership

Current authoritative owners:

- `Config`
  persisted settings, applet prefs, first-run defaults, and on-disk save/load
  policy
- `DockModel`
  visible items, item ordering, applet lifecycle, and item runtime state
- `DockWindow`
  shell-level pointer coordinates and active geometry frames
- `AutoHideController`
  hide/show state, delays, animation progress, hide offset, zoom progress
- `DockPlacementController`
  monitor and placement state
- `HoverManager`
  currently hovered item and preview show timer
- `PreviewPopup`
  preview hide timer and preview popup lifecycle

## Applet Architecture

Base API:

- `docking/applets/base.py`

Registry:

- `docking/applets/__init__.py`

Applet responsibilities:

- icon rendering or icon composition
- click/scroll behavior
- applet-specific timers/signals
- menu items
- persisted applet preferences

Design rule:

- applets should own their own behavior and presentation contracts
- renderer and dock window should deal in `DockItem` state and the applet API,
  not applet-specific internals

## Testing Map

Main test layout:

- `tests/core/`
- `tests/platform/`
- `tests/ui/`
- `tests/applets/`

Important current regression areas:

- geometry contracts and edge symmetry
- autohide state-machine behavior and reversal continuity
- renderer structure and anti-flicker behavior
- menu/preview/hover integration
- applet behavior and packaging helpers

## Packaging and Release Surfaces

Version surfaces that must stay in sync:

- `pyproject.toml`
- `setup.cfg`
- `docking/__init__.py`
- `packaging/rpm/docking.spec`
- `packaging/snap/snapcraft.yaml`

Release tooling also touches:

- `tools/bump_version.py`
- packaging metadata for Arch, Nix, Debian, and Flatpak

Recent maintenance notes:

- `tools/bump_version.py` is expected to be idempotent when asked to bump to
  the version already present in packaging metadata
- the release pipeline now publishes both x64 and arm64 artifacts where the
  package format is architecture-specific, while Debian remains a shared
  `linux-all.deb`
- CI uses explicit ARM64 test/build jobs in addition to the x64 matrix

## Current Status vs Planned Direction

The architecture is materially ahead of where it was when the original
`ARCHITECTURE.md` was written:

- geometry is shared and explicit
- UI assembly is explicit
- placement, interaction, and runtime surfaces are split into their own modules
- autohide behavior is more coherent and the historical jump bug is fixed in
  the state machine
- startup/runtime assembly now includes delayed post-show startup and IPC item
  service wiring
- config/theme behavior now includes first-run dock seeding, transparency, and
  crash-safe persistence

But some work is still intentionally described as planned rather than complete:

- a fully containment-led hover/autohide authority is still future work

That distinction matters. This document should remain a map of the current
codebase, while the parity and refactor docs describe either the status of a
subsystem or the next architectural step.
