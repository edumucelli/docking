# OOP Quality Measurement and Improvement Plan

This plan defines how Docking will measure object-oriented design quality before
large refactors, then use those measurements to guide small, reviewable cleanup
PRs.

The goal is not to optimize for one number. The goal is to make coupling,
cohesion, size, and complexity visible enough that architectural cleanup can be
prioritized and verified.

## Measurement Toolkit

Docking uses external tools for the baseline:

- `radon` for cyclomatic complexity and maintainability index.
- `lizard` for function NLOC, cyclomatic complexity, token count, and parameter
  count.
- `cohesion` for class cohesion.
- `import-linter` for package dependency contracts.

Install them with the existing development extra in an isolated metrics
environment:

```bash
python3 -m venv .venv-metrics
source .venv-metrics/bin/activate
python -m pip install -e ".[dev]"
```

Do not use `--system-site-packages` for this metrics environment. These tools
only parse source and import graphs; they do not need GTK or PyGObject from the
runtime environment. Keeping the metrics environment isolated also avoids
dependency collisions from globally visible packages. Locally, `import-linter
2.11` pulled `rich 15`, which conflicts with packages that require `rich < 14`
when they are exposed through `--system-site-packages`.

Run the measurement set from the repository root:

```bash
python -m radon cc docking -s -a
python -m radon mi docking -s
python -m lizard -l python docking -C 12 -a 6 -w
cohesion --files $(git ls-files 'docking/*.py' 'docking/**/*.py') --below 50
lint-imports --config pyproject.toml
```

The first baseline should be recorded in this file or a follow-up metrics
appendix before any structural cleanup PR. New gates should start as "no worse
than baseline" instead of strict pass/fail thresholds.

`lizard -w` exits non-zero when it finds current threshold warnings. That is
expected for the initial baseline. Treat those warnings as measurement output
until the project explicitly chooses CI gates.

Interpret the tools together, not as independent refactor mandates:

- `radon cc` is the strongest signal for functions whose branches need
  extraction or simplification.
- `radon mi` is module-level triage. It is useful for finding very large or
  dense files, but it does not identify the exact class boundary to split.
- `lizard` combines complexity, function length, token count, and parameter
  count. Parameter-only warnings are common for GTK callbacks and drawing
  helpers, so they should be fixed only when the new shape improves ownership.
- `cohesion` is useful for behavior-heavy classes. Data-only dataclasses, enums,
  and value objects often report `0.0%`; ignore those when prioritizing cleanup.
- `import-linter` is the hard boundary check. Its explicit exceptions are the
  dependency debt register.

## Current Baseline Findings

A first AST/import-graph inspection of the current tree found 274 Python files
under `docking/` with `git ls-files 'docking/*.py' 'docking/**/*.py'`.

Largest and lowest-cohesion class candidates:

| Class | Approx. LOC | Methods | Attributes | Concern |
| --- | ---: | ---: | ---: | --- |
| `docking.ui.menu.MenuHandler` | 1697 | 74 | 98 | Context menus, item menus, folder stacks, file monitors, popup lifecycle |
| `docking.ui.renderer.DockRenderer` | 879 | 17 | 18 | Large rendering pipeline, but likely cohesive |
| `docking.ui.settings.SettingsWindowController` | 822 | 38 | 69 | Multiple preferences pages and widget binding responsibilities |
| `docking.ui.dock_window.DockWindow` | 809 | 33 | 72 | GTK shell, event routing, UI collaborator ownership |
| `docking.applets.bluetooth.applet.BluetoothApplet` | 643 | 37 | 44 | Applet UI plus Bluetooth action coordination |
| `docking.platform.model.DockModel` | 632 | 31 | 25 | Item model plus live applet ownership |
| `docking.applets.network.applet.NetworkApplet` | 597 | 29 | 42 | Applet UI plus network action coordination |
| `docking.ui.dnd.DnDHandler` | 571 | 20 | 31 | Drag/drop policy and visual effects |

Highest runtime fan-out modules:

| Module | Runtime imports out | Imported by | Concern |
| --- | ---: | ---: | --- |
| `docking.ui.dock_window` | 23 | 1 | UI composition root has broad direct knowledge |
| `docking.ui.menu` | 13 | 1 | Menu subsystem owns several unrelated surfaces |
| `docking.applets.weather.applet` | 12 | 0 | Applet controller with API, state, render, UI, and prefs wiring |
| `docking.applets.certwatch.applet` | 11 | 0 | Applet controller with networking and UI coordination |
| `docking.app` | 11 | 0 | Startup composition root, expected to have fan-out |

Boundary issues found by import inspection:

- `docking.core.config` imports applet identity helpers and `platform.launcher`.
- `docking.platform.model` imports applet discovery and applet metadata.
- Several applet render modules import `docking.ui.overlays`.
- `docking.applets.popup` imports `docking.ui.display`.

Type-checking imports create apparent UI import cycles in naive analysis, but
runtime imports are currently acyclic. The refactor target is still to reduce
type-only dependency on `DockWindow` by introducing narrower protocols.

Measured tool highlights:

| Tool | Highest-signal findings |
| --- | --- |
| `radon cc` | `DockRenderer._draw_content` is `F (55)`. The next highest functions are `AiUsageApplet._build_tooltip_widget` at `D (24)`, `DockModel.update_running` at `D (23)`, `DockWindow._on_button_release` at `C (20)`, `Launcher.resolve` at `C (19)`, and several window/app parsing functions at `C (18)`. |
| `radon mi` | Only `docking.applets.music.state` and `docking.ui.menu` are `C (0.00)`. The current `B` modules are `docking.applets.network.applet`, `docking.applets.bluetooth.applet`, `docking.applets.bluetooth.state`, `docking.ui.renderer`, `docking.applets.keyboardlayout.state`, `docking.applets.notifications.applet`, `docking.applets.hackernews.applet`, and `docking.platform.unity`. `docking.ui.settings` is just above that band at `A (19.12)`. |
| `lizard` | Current warning thresholds flag 82 functions. The highest-signal warnings are `DockRenderer._draw_content`, `MenuHandler._list_directory`, `DockModel.update_running`, `DockWindow._on_button_release`, `AiUsageApplet._build_tooltip_widget`, `HoverManager.update`, and applet parsing/backend functions. Some warnings are parameter-count-only drawing or callback helpers. |
| `cohesion` | For behavior-heavy classes, the lowest large controllers are `MenuHandler` at `4.41%`, `SettingsWindowController` at `8.01%`, `BluetoothApplet` at `9.87%`, `DockWindow` at `10.53%`, `NetworkApplet` at `10.90%`, `DockRenderer` at `17.65%`, and `DockModel` at `23.66%`. |
| `import-linter` | All three baseline contracts are kept with the current explicit exceptions. |

## Architectural Contracts

The initial import-linter contracts are intentionally baseline-aware:

- `core` must not depend on `applets`, `platform`, or `ui`.
- `platform` must not depend on `ui`.
- `applets` must not depend on `ui` internals.

Known current exceptions are listed in `pyproject.toml` as `ignore_imports`.
Each cleanup PR should remove at least one exception when it fixes the
underlying dependency.

Do not add CI enforcement until:

1. The baseline command runs reliably in the local development environment.
2. Existing exceptions are documented.
3. The first cleanup PR proves the direction by removing at least one exception.

## Metric-Driven Remediation Plan

The current metrics point to a small number of structural problems. The plan
below treats each metric as evidence, then maps it to code movement that should
reduce coupling or complexity without changing behavior.

The numbered sections group problem areas. They are not the implementation
order. Implementation should be ordered by maintenance utility first, metric
impact second.

Utility-ranked implementation order:

1. Extract folder browsing from `MenuHandler`.
   This has the best utility/metric/risk ratio: it removes real IO, cache,
   sorting, icon, and child-probing responsibility from the worst MI/cohesion
   module, and the behavior is already covered by menu integration tests.
2. Move folder stack into its own module.
   The code already behaves like a subsystem: constants, card/layout dataclasses,
   popup state, file monitor, reveal/hover animation, drawing, hit testing, and
   cache prewarm all belong together. Do this after folder browsing so the new
   module can own a clean `FolderBrowser` directly instead of routing browser
   behavior through `MenuHandler` callbacks.
3. Split `docking.applets.music.state` by backend.
   This is a low-risk module-size fix with strong existing tests. It improves
   maintainability without inventing abstractions.
4. Add an AI usage tooltip view model.
   This removes business calculation from GTK widget construction and directly
   addresses a high CC function with a small, testable change.
5. Split `DockModel.update_running` using typed running-app data.
   This is high utility because pinned/transient state is core dock behavior.
   Start at the model boundary before changing Wnck scanning or launcher lookup.
6. Apply simple import-boundary wins.
   Move applet ID grammar to core and move overlay/display helpers only when the
   destination is obvious. Do not start with broad factories.
7. Split `WindowTracker` matching and aggregation after the model data shape is
   stable.
   This is useful, but more behavior-sensitive because it touches live window
   matching and focus/cycle behavior.
8. Split `DockRenderer._draw_content` only around repeated render-pass data.
   The function is the highest CC hotspot, but renderer responsibilities are
   comparatively cohesive. Avoid turning one readable draw pass into many
   indirect layers.
9. Extract `DockWindow` click routing.
   Useful and testable, but lower priority than model/menu because current
   complexity is localized to one event handler.
10. Thin Network and Bluetooth applet controllers when their backend/menu code is
   next being changed.
   They are real cohesion problems, but moving code without a cleaner state or
   backend seam would be file churn.
11. Split settings pages only after binding extraction proves useful.
    The class is low-cohesion, but a page-object graph can be more complex than
    the current single controller if done only for cohesion numbers.

Changes that can make the code worse if done only for metrics:

| Proposed change | Risk | Utility-based rule |
| --- | --- | --- |
| Full applet registry/factory for `DockModel` | Adds abstract factory language around a small concrete dependency. | Start with simple injected callables or a tiny concrete registry only when moving applet discovery out of platform model. |
| `Config.load(initial_pinned_factory=...)` | Adds a callback API to config just to satisfy import-linter. | Prefer a small bootstrap helper in `docking.app`; add a factory argument only if preserving first-run behavior requires it. |
| Renderer context dataclasses | Can hide simple local variables behind mutable pass objects. | Add `RenderItem` only if it removes repeated bounce/position/drop-shift computation. Add `RenderPassContext` only if helper signatures stay noisy after that. |
| Settings page classes | Can spread one sync problem across many objects. | Extract `SettingsBindingRegistry` first; split pages only if tests or future settings work become easier. |
| `WindowMatcher` plus many running-window objects | Can create too many tiny abstractions around Wnck calls. | Start with `RunningAppInfo` and model split; extract `WindowMatcher` only after tests show matching rules need isolated coverage. |
| Network/Bluetooth menu modules | Can become a file move with the same mixed GTK/backend responsibilities. | First define a snapshot/view-model boundary, then move menu construction. |
| Lizard parameter-count fixes | Can replace clear callback/drawing signatures with opaque context objects. | Ignore parameter-only warnings unless the same parameter group repeats across several helpers. |
| Cohesion-driven splitting of dataclasses/enums/value objects | Produces meaningless work because cohesion reports `0.0%` for data-only objects. | Ignore data-only cohesion results entirely. |

### 1. Import-Linter Boundary Debt

Problem:

- `docking.core.config` imports `docking.applets.identity` only to recognize and
  format applet desktop IDs.
- `docking.core.config` imports `docking.platform.launcher` at first-run load
  time to probe starter desktop files.
- Several applet render modules import `docking.ui.overlays`.
- `docking.applets.popup` imports `docking.ui.display`.
- `docking.platform.model` imports applet discovery and separator metadata. This
  is not blocked by the initial contracts yet, but it is the same ownership
  issue.

Why the metrics flag it:

- `import-linter` keeps the baseline only through explicit exceptions.
- `core.config` also has `PinnedEntry.from_raw` at `C (13)`, partly because it
  mixes persisted shape parsing with applet URI knowledge and file URI probing.

Best fix:

- Create a core identity module, for example `docking.core.identity`, containing
  applet desktop ID parsing/formatting and typed item ID helpers. Move
  `applet_desktop_id`, `is_applet_desktop_id`, and related parsing there.
- Keep applet catalog metadata in `docking.applets.identity`; only generic ID
  grammar moves to core.
- Move first-run launcher probing out of `Config.load`, but keep the public
  config API simple. Prefer a bootstrap helper in `docking.app` that creates the
  first-run pinned list and passes it into config creation. Add a
  `Config.load(..., initial_pinned_factory=...)` argument only if preserving the
  exact current first-run behavior cannot stay readable without it.
- Move shared overlay drawing into `docking.applets.overlays` or
  `docking.applets.draw` because these helpers are used by applet renderers and
  do not need UI window/menu concepts.
- Move `clamp_to_screen` and `get_pointer_position` into a neutral display
  geometry module that applet popups and UI can both use.
- When `DockModel` is next being changed for applet lifecycle work, introduce
  the smallest applet provider it needs: create applet, read metadata, create
  separator IDs. Do not start with a broad abstract factory.

First PR shape:

- Move only applet ID grammar to core and update callers.
- Remove the `docking.core.config -> docking.applets.identity` exception.
- Update callers in the same PR so the codebase does not start with a
  half-migrated identity API.
- Leave first-run launcher probing and applet provider injection for separate
  PRs so this change stays small and easy to review.

Acceptance:

- `lint-imports --config pyproject.toml` still passes with one fewer exception.
- `tests/core/test_config.py`, the identity tests, `tests/platform/test_model.py`,
  and `tests/test_app.py` pass.

### 2. `docking.ui.menu` And `MenuHandler`

Problem:

- `docking.ui.menu` is `radon mi C (0.00)`.
- `MenuHandler` is the lowest large cohesion result at `4.41%`.
- Lizard flags menu methods such as `_list_directory`,
  `_on_folder_stack_animation_frame`, `_build_item_menu`, and `_build_dock_menu`.
- The class owns unrelated state: plain popup menus, item menus, dock background
  menus, folder stack window lifecycle, folder stack drawing, directory listing,
  folder cache, file monitors, folder prefs, and animation.

Why the metrics flag it:

- Menu construction and folder browsing are not one responsibility.
- `_list_directory` does IO, cache lookup, hidden-file filtering, child probing,
  icon resolution, row construction, and sorting.
- Folder stack drawing/animation has a separate lifecycle from right-click
  menus but shares the same object fields.

Best fix:

- Done: extract a private folder browsing service in
  `docking.ui.folder._browser`:
  - `FolderRow` dataclass instead of `dict[str, Any]`.
  - `FolderPrefs` dataclass for sort, hidden files, and icon size.
  - `FolderBrowser.list_directory(target, prefs, icon_px)` for enumeration,
    child checks, icon resolution, caching, and sorting.
  - `FolderBrowser.invalidate(target)` for monitor callbacks.
- Done: move folder stack into `docking.ui.folder.stack`:
  - `FOLDER_STACK_*` constants.
  - `FolderStackCard`, `FolderStackCardGeometry`, and `FolderStackLayout`.
  - layout cache and prewarm queue.
  - popup window lifecycle.
  - file monitor and refresh debounce.
  - reveal/hover animation.
  - card drawing, hit testing, and target activation.
- Done: make `FolderStackController` own `FolderBrowser` directly. `MenuHandler`
  no longer imports folder browser internals or passes browser-shaped callbacks
  such as `list_directory`, `folder_prefs`, `target_state`, `cache_stamp`, or
  `invalidate_target`.
- Extract `FolderMenuController` for recursive folder submenus and directory
  monitors.
- Extract `ItemContextMenuBuilder` and `DockContextMenuBuilder` for right-click
  menu contents. They should receive small collaborator protocols instead of the
  whole `MenuHandler`.
- Leave `MenuHandler` as a thin facade that hit-tests, opens the correct menu,
  and coordinates lifecycle with `DockRuntime`.

First PR shape:

- Done: start with `FolderBrowser` plus `FolderRow`, then hide it behind
  `FolderStackController` so `MenuHandler` only depends on the folder stack
  facade.
- Done: this isolates IO and cache behavior before moving any popup lifecycle code.
- Done: next move all folder stack-specific code to `docking.ui.folder.stack` in one
  PR. Keeping the stack code together is better than sprinkling constants,
  layout, drawing, and animation across several generic menu helpers.
- After that, extract recursive right-click folder menus if `MenuHandler` still
  owns too much menu-specific file-monitor state.

Acceptance:

- Done: `MenuHandler` no longer owns directory enumeration details.
- Done: `MenuHandler` no longer owns `_folder_stack_*` state.
- Done: `_list_directory` is removed from `MenuHandler`.
- Done: `MenuHandler` has no `FolderBrowser` import and no folder stack callback
  constructor glue.
- Done: `show_folder_stack`, `close_folder_stack`, `open_folder_stack_item_id`, and
  stack prewarm become delegations to the folder stack module.
- Partial: `tests/ui/test_menu_integration.py` now targets
  `FolderStackController` ownership explicitly, but the focused tests should
  still be split into module-specific files.
- `radon mi docking/ui/menu.py -s` should improve from `C (0.00)`, even before
  the full class split.

### 3. `DockRenderer._draw_content`

Problem:

- `DockRenderer._draw_content` is the largest cyclomatic hotspot at `F (55)`.
- Lizard reports `305` NLOC, `55` CCN, `10` parameters, and `340` length.
- `DockRenderer` cohesion is not as bad as menu, but this one method mixes
  shelf smoothing, shelf drawing, active/drop glows, slide offsets, hover
  lightening, click darkening, launch/urgent bounce, icon drawing, indicators,
  badges, progress bars, and hidden urgent glow.

Why the metrics flag it:

- There are several repeated per-item computations. Bounce and mapped icon
  position are computed once for icons and again for app overlays.
- The method handles the full render pass imperatively, so every new visual
  state adds branches to the same function.

Best fix:

- Add the smallest render data object that removes repeated work:
  - `RenderItem`: item, layout item, index, slide, drop shift, bounce, darken,
    lighten, mapped x/y, scaled size, expected draw rect.
- Add `RenderPassContext` only if the extracted phase signatures remain noisy
  after `RenderItem` exists. Avoid turning simple local state into a bag of
  mutable attributes.
- Extract render phases:
  - `_prepare_render_context(...)`.
  - `_sync_shelf_extent(...)`.
  - `_draw_shelf(...)`.
  - `_draw_active_and_drop_glows(...)`.
  - `_prepare_render_items(...)`.
  - `_draw_icons(...)`.
  - `_draw_running_indicators(...)`.
  - `_draw_app_overlays(...)`.
  - `_draw_hidden_urgent_glows(...)`.
- Keep low-level drawing helpers where they are. The first goal is a readable
  pipeline, not a new renderer hierarchy.

First PR shape:

- Introduce `RenderItem` and compute bounce/position once.
- Split only the icon, indicator, overlay, and hidden urgent passes. Do not
  change Cairo drawing helpers in the same PR.
- Do not chase the `F (55)` score by creating many one-call helper methods that
  obscure the draw order.

Acceptance:

- Existing renderer visual tests and integration tests pass.
- `DockRenderer._draw_content` falls below `C (20)` or becomes a thin pipeline
  that delegates each phase.
- Lizard warnings for repeated parameter groups should drop after introducing
  `RenderPassContext`.

### 4. Running-App Reconciliation: `DockModel`, `WindowTracker`, `Launcher`

Problem:

- `DockModel.update_running` is `D (23)`.
- `WindowTracker._update_running` and `_match_window` are both `C (18)`.
- `Launcher.resolve` is `C (19)`.
- These three functions form one workflow: discover windows, match them to
  desktop IDs, update pinned/transient items, and apply Unity launcher overlay
  state.

Why the metrics flag it:

- `WindowTracker` mixes Wnck error handling, filtering, active window detection,
  matching, aggregation, urgent state, XID cache updates, and cycle-order cleanup.
- `DockModel.update_running` mixes pinned reset, pinned update, transient
  construction, launcher-only transient creation, sender-to-item remapping, and
  notification.
- `Launcher.resolve` mixes Gio lookup, XDG file search fallback, WM class
  fallback, icon/name extraction, and WM class index mutation.

Best fix:

- Introduce typed running data:
  - `RunningWindowInfo`: desktop ID, xid, active, urgent, Wnck window reference.
  - `RunningAppInfo`: count, active, urgent, windows, xids.
- In `WindowTracker`, split scanning into:
  - `_iter_tasklist_windows()`.
  - `_active_xid()`.
  - `_window_snapshot(window)`.
  - `_aggregate_running(windows)`.
  - `_cleanup_cycle_state(active_desktop_ids)`.
- In matching, create a `WindowMatcher` collaborator that owns WM_CLASS maps,
  candidate IDs, missed candidates, and `resolve_by_wm_class` fallbacks.
- In `DockModel`, split update into:
  - `_reset_pinned_running_state()`.
  - `_apply_running_to_pinned(running) -> matched_ids`.
  - `_build_running_transients(running, matched_ids, existing_transient)`.
  - `_apply_launcher_entries(items_by_desktop_id, new_transient, existing_transient)`.
- In `Launcher`, split desktop lookup from metadata construction:
  - `_desktop_app_info_for_id(desktop_id)`.
  - `_desktop_app_info_from_xdg_dirs(desktop_id)`.
  - `_wm_class_for_app_info(app_info, desktop_id)`.
  - `_desktop_info_from_app_info(desktop_id, app_info)`.

First PR shape:

- Start with typed `RunningAppInfo` and split `DockModel.update_running`.
- This is well covered by `tests/platform/test_model.py` and has no GTK drawing
  dependency.

Acceptance:

- `DockModel.update_running` drops below `C (10)`.
- `tests/platform/test_model.py`, `tests/platform/test_window_tracker.py`, and
  `tests/platform/test_window_tracker_integration.py` pass.
- No change to visible pinned/transient ordering.

### 5. `SettingsWindowController`

Problem:

- `SettingsWindowController` has low cohesion at `8.01%`.
- It owns window lifecycle, page construction, widget inventory, scalar binding,
  applet catalog/grid building, update status/actions, theme application, hide
  mode descriptions, and dependent sensitivity.
- It is not a low maintainability module yet, but it is a clear future growth
  risk.

Why the metrics flag it:

- Page-specific widgets are stored as controller-wide fields.
- `_register_bindings` knows every page and every runtime side effect.
- The applets page has separate catalog grouping and icon loading behavior from
  appearance/behavior scalar settings.

Best fix:

- Extract a small `SettingsBindingRegistry` that owns `_ScalarBinding`,
  registration, sync, and change dispatch.
- Extract page builders:
  - `AppearanceSettingsPage`.
  - `BehaviorSettingsPage`.
  - `AppletSettingsPage`.
  - `UpdateSettingsPage`.
- Give each page only the config/runtime/model collaborators it needs.
- Keep `SettingsWindowController` as the window owner that builds the stack,
  calls page sync, and handles destroy/present.

First PR shape:

- Extract `SettingsBindingRegistry` first. That removes the broadest shared
  field and makes page extraction much less risky.
- Do not split pages first. Page classes are useful only after binding and sync
  no longer require the parent controller to know every widget.

Acceptance:

- `SettingsWindowController` no longer stores every scalar widget directly.
- `tests/ui/test_settings.py` remains green, with new focused tests for the
  binding registry.
- The controller's cohesion should improve before page extraction is complete.

### 6. `DockWindow._on_button_release`

Problem:

- `_on_button_release` is `C (20)`.
- The method handles drag threshold checks, right-click menu opening, hit
  testing, animation timestamp changes, applet clicks, folder stack toggles,
  file opening, app launching, configured left/middle click actions, modifier
  overrides, window cycling, minimize, close, focus toggle, tooltip refresh, and
  animation pump duration.

Why the metrics flag it:

- Raw GTK event handling and click policy live in the same method.
- The action selection logic is product policy and can be tested without a
  `DockWindow`.

Best fix:

- Add a `DockClickRouter` under `docking.ui.interaction` or
  `docking.ui.clicks`.
- Input: button, modifier state, item kind, running state, configured left and
  middle actions.
- Output: a typed command such as `OpenMenu`, `AppletClick`, `OpenFolderStack`,
  `OpenFile`, `Launch`, `LaunchNewWindow`, `CycleWindows`,
  `ActivateMostRecent`, `MinimizeWindows`, `CloseFocused`, or `ToggleFocus`.
- `DockWindow._on_button_release` should become: validate drag threshold,
  hit-test, ask router for command, execute command through existing
  collaborators, update animation timestamps.

First PR shape:

- Extract the pure command-selection function and test all button/action/modifier
  combinations.
- Leave command execution in `DockWindow` until the pure policy is covered.

Acceptance:

- `_on_button_release` drops below `C (10)`.
- `tests/ui/test_dock_window_integration.py` still covers execution behavior.
- New unit tests cover click routing without GTK construction.

### 7. Applet Controller Hotspots

Problem:

- `NetworkApplet` has low cohesion at `10.90%` and `docking.applets.network.applet`
  has low MI at `B (10.48)`.
- `BluetoothApplet` has low cohesion at `9.87%`; its backend/state file also has
  low MI at `B (14.26)`.
- `AiUsageApplet._build_tooltip_widget` is `D (24)`.
- `docking.applets.music.state` is `C (0.00)` because it contains state helpers,
  MPRIS DBus backend, playerctl backend, Rhythmbox backend, and hybrid fallback
  selection in one module.

Why the metrics flag it:

- Network and Bluetooth applets mix GTK menu construction, live backend calls,
  polling, prefs, and state projection.
- AI usage tooltip construction mixes filtering, totals, formatting, GTK label
  construction, and week summary logic.
- Music has several independent backend implementations in one file. That is a
  module-size/maintainability problem more than an object cohesion problem.

Best fix:

- Network:
  - Add `NetworkSnapshot` as the single state object consumed by render,
    tooltip, and menu code.
  - Move NetworkManager reads and actions to `NetworkManagerBackend`.
  - Move Wi-Fi/VPN menu construction to `network/menu.py`.
  - Leave `NetworkApplet` with lifecycle, polling timer, prefs, and `present()`.
- Bluetooth:
  - Move menu construction to `bluetooth/menu.py`.
  - Move discovery/power/recent-connection coordination to a controller that can
    be tested with a fake `BluezBackend`.
  - Consider moving `BluezBackend` out of `state.py` into `backend.py` so
    `state.py` returns to dataclasses, parsing, tooltip text, and pure helpers.
- AI usage:
  - Build a pure tooltip view model first: header, model rows, optional week
    row.
  - Let `_build_tooltip_widget` render that view model to GTK labels.
- Music:
  - Split `state.py` into `state.py`, `mpris.py`, `playerctl.py`,
    `rhythmbox.py`, and `hybrid.py`.
  - Keep behavior unchanged and update tests to import the new backend modules.

First PR shape:

- Start with Music module split or AI tooltip view model. Both are low-risk
  because they are already heavily unit tested and mostly independent of GTK.
- Then handle Network and Bluetooth with fake backend tests before moving menu
  code.

Acceptance:

- `radon mi docking/applets/music/state.py -s` is no longer `C (0.00)` because
  backends move out.
- `AiUsageApplet._build_tooltip_widget` drops below `C (10)`.
- `NetworkApplet` and `BluetoothApplet` lose backend/menu details while existing
  applet tests stay green.

### 8. Lower-Signal Lizard Warnings

Problem:

- Lizard currently flags 82 functions.
- Some high-parameter warnings are drawing helpers, GTK callbacks, or generic
  worker helpers where the parameter count is explicit and understandable.

Best fix:

- Do not chase all warnings mechanically.
- Use a context dataclass only when the same parameter group is passed through
  several functions, such as renderer pass state or folder stack drawing state.
- Leave GTK signal callback signatures alone unless a callback also has high
  complexity or mixed responsibility.

Acceptance:

- CI gates, when added, should be "no new warnings above baseline" first.
- Strict thresholds should come only after the main structural splits reduce the
  baseline naturally.

## Testing and Acceptance Criteria

For the measurement PR:

- `python -m radon cc docking -s -a`
- `python -m radon mi docking -s`
- `python -m lizard -l python docking -C 12 -a 6 -w`
- `cohesion --files $(git ls-files 'docking/*.py' 'docking/**/*.py') --below 50`
- `lint-imports --config pyproject.toml`
- `git diff --check`

For each cleanup PR:

- Run the relevant targeted tests, for example:
  - `python -m pytest tests/ui/test_menu.py tests/ui/test_menu_integration.py -q`
  - `python -m pytest tests/ui/test_settings.py -q`
  - `python -m pytest tests/platform/test_model.py tests/applets/test_registry.py -q`
  - applet-specific tests under `tests/applets/`.
- Rerun the relevant metric command and compare against the baseline.
- Remove any import-linter exception that is no longer needed.
- Run the full suite before merge.

## Guardrails

- Do not weaken production contracts to make metrics look better.
- Do not split classes only by line count; split around ownership boundaries.
- Do not introduce abstract factories unless they remove a concrete dependency
  or make tests materially simpler.
- Keep PRs small enough that behavior can be reviewed independently from
  movement.
- Prefer "measure, refactor one boundary, verify" over broad mechanical churn.
