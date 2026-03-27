# Simplification Plan

This document turns the simplification review into concrete changes.
It focuses on code shape, ownership, and explicit contracts rather than new
features.

The goal is not to rewrite the dock in one pass. The goal is to reduce the
places where state, policy, and side effects are duplicated across modules.

## Scope

This plan covers these review points:

1. Replace `DockModel.on_change` single-slot callbacks with explicit listeners.
2. Remove duplicated runtime reconciliation between `WindowTracker` and
   `DockModel`.
3. Split remaining god-object behavior out of `DockWindow` and `MenuHandler`.
4. Consolidate duplicated settings actions shared by menu and preferences UI.
5. Split config value modeling from config persistence and migration.
6. Make theme loading spec-driven instead of hand-parsed field by field.
7. Make applet discovery truly lazy and keep applet package `__init__.py`
   metadata-only.
8. Strengthen shared applet infrastructure so applets stop reimplementing the
   same timers, workers, and popup scaffolding.
9. Replace duplicated IPC method definitions with one structured contract.

## Priority Order (Gain vs Complexity)

This execution order reflects the codebase as it exists now. Some items are
fairly self-contained, while others are tightly coupled to several runtime
boundaries and should be deferred until the contract work is clearer.

Recommended priority order:

1. `DockModel` listener refactor
2. shared settings actions
3. theme loader cleanup
4. IPC method table cleanup
5. applet infrastructure expansion
6. applet discovery cleanup
7. config split
8. runtime reconciliation refactor
9. deeper UI decomposition around `DockWindow` and `MenuHandler`

Why this order:

- items 1 and 2 have unusually good payoff for their size and remove real
  duplication at active boundaries
- item 6 is valuable, but it is not a "simple startup fix"; it is a
  package import-surface migration
- items 7, 8, and 9 are broad refactors and should not
  move ahead of smaller, more certain wins
- item 3 is more deeply entangled with drag, hover, preview, autohide, and GTK
  event semantics than the rest of the list

## Complexity / Gain Summary

This is the assessment for the code as it exists now:

1. `DockModel` listeners
   Gain: high
   Complexity: low to medium
   Reason: only a few owners exist, but it removes fragile callback chaining
   and unlocks cleaner IPC/UI boundaries.
2. Shared settings actions
   Gain: medium to high
   Complexity: low to medium
   Reason: duplication is concrete and localized across menu/settings code.
3. Theme loader cleanup
   Gain: medium
   Complexity: low to medium
   Reason: the code is local, but the existing scale and fallback rules are
   subtle and must be preserved exactly.
4. IPC method table cleanup
   Gain: medium
   Complexity: low to medium
   Reason: contained module pair, but the external API contract must stay
   stable.
5. Applet infrastructure expansion
   Gain: medium
   Complexity: medium
   Reason: worth doing incrementally; expensive only if forced into a
   one-framework rewrite.
6. Applet discovery cleanup
   Gain: medium to high
   Complexity: medium to high
   Reason: startup and clarity win, but package-level imports are already part
   of tests and docs.
7. Config split
   Gain: high
   Complexity: high
   Reason: broad design payoff, but `Config.load()` / `save()` are pervasive.
8. Runtime reconciliation refactor
   Gain: high
   Complexity: very high
   Reason: `WindowTracker` is also a live action service, not just a scanner.
9. `DockWindow` / `MenuHandler` decomposition
   Gain: very high
   Complexity: extreme
   Reason: highest long-term payoff, but it sits in the densest interaction
   area of the codebase and should come last.

## Suggested Phases

If this work is executed across several change sets, use these phases:

### Phase 1: bounded, high-confidence wins

1. `DockModel` listener refactor
2. shared settings actions
3. theme loader cleanup
4. IPC method table cleanup

### Phase 2: incremental structural cleanups

5. applet infrastructure expansion
6. applet discovery cleanup

### Phase 3: high-coupling refactors

7. config split
8. runtime reconciliation refactor
9. deeper UI decomposition around `DockWindow` and `MenuHandler`

## Architectural Wrinkles To Resolve First

The sections below describe real simplification targets, but several of them
still contain design assumptions that have not been validated deeply enough in
code yet. These are the highest-priority unknowns to settle before starting the
larger refactors:

1. Applet package `__init__.py` is currently both discovery metadata and a
   public import surface. Making it metadata-only will require a deliberate
   migration plan for tests, docs, and any supported package-level imports.
2. `WindowTracker` is not only a scan/reconcile layer. It is also the live
   window action service used by menus and previews. Any split between tracker
   and model must define who keeps ownership of live `Wnck.Window` actions.
3. `Config` is used as both value object and persistence API throughout the
   codebase. Splitting it into `Config` plus `ConfigStore` will require a staged
   caller migration rather than a simple internal extraction.
4. Replacing `DockModel.on_change` with listeners is the right direction, but
   callback ordering and teardown semantics must be specified up front because
   the current IPC and UI layers rely on them implicitly.
5. IPC simplification should focus on removing duplicated method metadata, not
   blindly replacing static XML with generated XML. A generated contract is only
   useful if it stays clearer than the current static definition.

## 1. Replace `DockModel.on_change` With Explicit Listeners

Status: done

### What is implemented now

- `DockModel` exposes `add_change_listener()` and
  `remove_change_listener()`.
- `DockModel.notify()` fans out over registered listeners instead of using a
  single mutable callback slot.
- Listeners are deduplicated and currently fire in registration order.
- `notify()` iterates over a shallow copy, so listeners can unsubscribe during
  callback execution without breaking delivery.
- `DockItemsService` subscribes on `start()` and unsubscribes on `stop()`.
- `DockWindow` subscribes during initialization and unsubscribes on destroy.
- Tests cover listener registration, removal, registration-order delivery,
  self-removal during notification, IPC subscription teardown, and dock-window
  destroy teardown.

### Problem addressed

`DockModel` exposes exactly one mutable callback slot:

- `docking/platform/model.py`
- `docking/ipc/items_service.py`
- `docking/ui/dock_window.py`

That creates hidden ownership rules:

- `DockWindow` assumes it can assign `model.on_change`
- `DockItemsService` wraps and restores the same slot
- tests assert exact callback identity instead of observable behavior

This is fragile because adding one more subscriber requires callback chaining or
more slot hijacking.

### Implemented changes

1. Add explicit listener APIs to `DockModel`:
   - `add_change_listener(callback)`
   - `remove_change_listener(callback)`
   - internal `_listeners: list[Callable[[], None]]`
2. Keep notification firing in one place:
   - replace direct `if self.on_change: self.on_change()` logic with iteration
     over listeners
   - iterate over a shallow copy so listeners can unregister during callbacks
3. Update `DockWindow` to subscribe during setup and unsubscribe during teardown
   instead of assigning `model.on_change`.
4. Update `DockItemsService` to subscribe/unsubscribe directly instead of
   capturing and restoring a previous callback.
5. Remove `on_change` from the public model contract entirely once all callers
   move to listeners.

### Unknowns and wrinkles

- Listener ordering is now registration order in practice and is covered by
  tests. If later refactors need a different policy, they should change that
  contract deliberately rather than incidentally.
- Teardown ownership is now explicit in the current code:
  `DockItemsService.stop()` and `DockWindow._on_destroy()` own their own
  unsubscription.
- A plain list is sufficient for the current listener count and lifecycle
  complexity. A dedicated notifier object is not warranted unless more emitters
  or disposable subscription semantics appear.

### Code areas

- `docking/platform/model.py`
- `docking/ipc/items_service.py`
- `docking/ui/dock_window.py`
- `docking/ui/factory.py`
- `tests/platform/test_model.py`
- `tests/ipc/test_items_service.py`
- `tests/ui/test_dock_window_integration.py`

### Current acceptance state

- no production module assigns `model.on_change = ...`
- multiple listeners can coexist without wrapping one another
- teardown paths remove listeners cleanly
- listeners fire in registration order
- tests assert observable event behavior instead of one callback slot identity

## 2. Remove Duplicate Running-App Reconciliation

### Current problem

`WindowTracker` and `DockModel` both participate in reconstructing running app
state:

- `docking/platform/window_tracker.py`
- `docking/platform/model.py`

Today the tracker builds a nested `dict[str, dict[str, Any]]`, and the model
interprets it again to decide:

- grouping
- launcher matching
- transient item creation
- attention/running state

That makes the real contract implicit and easy to break.

### Required changes

1. Introduce typed snapshot models in `docking/platform/`:
   - `WindowSnapshot`
   - `RunningAppSnapshot`
2. Make `WindowTracker` the only owner of raw Wnck window scanning and grouping.
3. Move launcher matching policy into one explicit collaborator:
   - either a `RunningAppResolver`
   - or a narrow helper owned by `WindowTracker`
4. Change `DockModel.update_running()` to consume typed snapshots instead of a
   nested dictionary.
5. Limit `DockModel` responsibility to:
   - merging snapshots into current dock items
   - pin/transient ordering
   - applet separators and pinned items already owned by the model
6. Remove data that is computed but not consumed, especially extra raw window
   collections that leak scan-layer details upward.

### Unknowns and wrinkles

- `WindowTracker` also serves the preview and menu layers directly for window
  titles, activation, close, and lookup by XID. The refactor cannot treat it as
  only a background reconciliation source.
- If `RunningAppSnapshot` becomes the tracker/model contract, the plan still
  needs a clear owner for live `Wnck.Window` handles used by preview and menu
  interactions.
- The cached XID mapping is part of the current race-avoidance strategy for
  hover-time UI paths. Any typed snapshot design must preserve that property,
  not just the shape of `update_running()`.

### Code areas

- `docking/platform/window_tracker.py`
- `docking/platform/model.py`
- `docking/platform/launcher.py`
- `tests/platform/test_window_tracker_integration.py`
- `tests/platform/test_model.py`

### Acceptance criteria

- tracker/model contract is a typed value, not a nested ad hoc dictionary
- launcher resolution happens in one place
- the model no longer needs to understand Wnck grouping details

## 3. Further Decompose `DockWindow` and `MenuHandler`

### Current problem

The UI is improved compared with older versions, but two large control points
still carry too much:

- `docking/ui/dock_window.py`
- `docking/ui/menu.py`

`DockWindow` still mixes shell ownership, event routing, hover transitions,
preview interaction, autohide coordination, and some redraw policy.
`MenuHandler` still builds several distinct menu systems:

- dock background menu
- app item menus
- folder menus
- live window menus
- settings-triggering actions

### Required changes

1. Extract a pointer interaction controller from `DockWindow`:
   - own enter/leave/motion/button/scroll routing
   - compute interaction intent from geometry frame + pointer context
   - tell shell/runtime what to redraw or show
2. Keep `DockWindow` focused on:
   - GTK window lifecycle
   - drawing area ownership
   - input shape / struts / barriers integration
   - delegating events to collaborators
3. Split menu construction into smaller builders:
   - `DockContextMenuBuilder`
   - `ItemMenuBuilder`
   - `FolderMenuBuilder`
   - `WindowListMenuBuilder`
4. Keep one thin `MenuHandler` only if it still adds value as an orchestration
   point. Otherwise rename it to reflect the smaller role.
5. Move runtime actions used by menus into a shared service rather than calling
   directly into many window/model/config collaborators.

### Unknowns and wrinkles

- `DockWindow` is already partly decomposed: interaction, geometry, placement,
  hover, autohide, tooltip, preview, and DnD all exist as separate modules. The
  remaining problem is not total absence of structure, but that the shell still
  owns too much of the final event-routing and redraw choreography.
- `MenuHandler` is not just "one more builder." It contains live window rows,
  folder monitoring, popup lifecycle, and preference mutations. Any split has
  to separate those responsibilities without recreating the same cross-calls in
  smaller classes.
- This item is easier after listeners, shared settings actions, and the tracker
  boundary are cleaner. Doing it earlier would mostly move code around without
  reducing coupling.

### Code areas

- `docking/ui/dock_window.py`
- `docking/ui/menu.py`
- `docking/ui/interaction.py`
- `docking/ui/runtime.py`
- `docking/ui/factory.py`
- `tests/ui/test_dock_window_integration.py`
- `tests/ui/test_menu.py`
- `tests/ui/test_menu_integration.py`

### Acceptance criteria

- `DockWindow` no longer owns most raw interaction policy directly
- menu builders are separated by concern rather than menu type branching
- unit tests can target smaller controllers without constructing the whole UI

## 4. Consolidate Settings Actions Shared by Menu and Preferences UI

### Current problem

Settings behavior is duplicated across:

- `docking/ui/menu.py`
- `docking/ui/settings.py`

The same change often appears in both places:

- mutate config
- save config
- update runtime side effects
- hide a tooltip or rebuild geometry
- queue redraw or reposition

That duplication makes behavior drift likely.

### Required changes

1. Create a shared action layer, for example `PreferencesActions`:
   - takes `Config`
   - takes a narrow runtime surface
   - exposes named actions such as `set_theme(name)`, `set_position(pos)`,
     `set_hide_mode(mode)`, `set_icon_size(size)`
2. Move the save-and-side-effect sequences into that service.
3. Make `MenuHandler` and `SettingsWindowController` call the same action
   methods instead of reproducing the logic.
4. Keep settings widgets responsible only for collecting user input and binding
   controls, not for re-encoding runtime policies.
5. Revisit generic setting binding code in `settings.py` so it triggers shared
   actions where runtime consequences exist.

### Code areas

- `docking/ui/menu.py`
- `docking/ui/settings.py`
- `docking/ui/runtime.py`
- possibly new `docking/ui/preferences_actions.py`
- tests in `tests/ui/test_menu_integration.py` and `tests/ui/test_settings.py`

### Acceptance criteria

- changing a setting follows one code path regardless of whether it came from
  the background menu or the preferences window
- runtime side effects are named and explicit
- theme, position, icon size, and hide mode changes no longer duplicate save
  and redraw logic

### Unknowns and wrinkles

- This cleanup should not accidentally centralize folder-menu preference writes
  or applet toggle behavior if they do not share the same runtime semantics.
- The current settings binding system mixes pure persistence changes and runtime
  side effects in one generic path. The plan needs a rule for which settings
  stay generic and which must route through named actions.

## 5. Split Config Value Modeling From Persistence and Migration

### Current problem

`Config` currently does too much in one type:

- schema/defaults
- persisted value normalization
- migration/legacy compatibility
- file I/O and path state

That lives mainly in `docking/core/config.py`.

### Required changes

1. Keep `Config` as a pure value model:
   - typed fields only
   - small validation in `__post_init__` if still needed
2. Introduce a persistence layer, for example `ConfigStore`:
   - knows the config file path
   - loads raw TOML/JSON data
   - applies migrations
   - serializes `Config` back to disk
3. Introduce a normalization layer if needed:
   - raw persisted strings -> enums / typed fields
   - missing fields -> defaults
4. Store enums as enums inside `Config`:
   - `Position`
   - `HideMode`
   - only convert to persisted string values at the store boundary
5. Simplify pinned item parsing so the legacy translation logic is isolated in
   one place rather than leaking across the model.
6. Make config path state explicit in `ConfigStore` instead of keeping hidden
   path ownership inside `Config`.

### Unknowns and wrinkles

- Many callers currently rely on `Config.load()` and `config.save()` directly.
  This is not only an internal cleanup; it requires a staged migration of UI,
  model, applet, and test code.
- `position` and `hide_mode` are persisted as strings but are also bound
  directly into GTK widgets. Moving them to enums in `Config` will require an
  explicit conversion boundary for widget bindings and saved payloads.
- `_path` currently lives inside `Config` and is part of the default save
  behavior. If a separate store owns paths, the plan must say whether callers
  still get a convenient `save()` path or whether persistence becomes explicit
  at call sites.

### Code areas

- `docking/core/config.py`
- `docking/core/position.py`
- `docking/app.py`
- `docking/ui/menu.py`
- `docking/ui/settings.py`
- tests under `tests/core/test_config.py`

### Acceptance criteria

- `Config` can be instantiated and reasoned about without file-system concerns
- load/save/migration behavior lives outside the value object
- enum handling is typed inside runtime code and stringified only at the
  persistence boundary

## 6. Make Theme Loading Spec-Driven

### Current problem

`Theme.load()` manually re-describes the theme schema field by field even
though `Theme` already exists as a dataclass in `docking/core/theme.py`.

That creates drift risk:

- defaults are declared in more than one place
- parsing logic is repetitive
- adding one field requires touching several branches

### Required changes

1. Define one theme specification table close to the `Theme` dataclass:
   - field name
   - expected type
   - validator/coercer
   - whether the field scales with icon size
2. Generate parsing from that spec instead of hand-writing one branch per
   property.
3. Use dataclass defaults as the source of truth whenever possible.
4. Keep validation/coercion helpers small and focused:
   - numeric bounds
   - color parsing
   - scale factors
5. Separate theme file reading from theme normalization:
   - raw JSON loader
   - typed `Theme` constructor path

### Unknowns and wrinkles

- `Theme.load()` is localized, but the rules it encodes are not trivial:
  scaling, unscaled fields, indicator-size special handling, `h_padding`
  fallback, and derived `shelf_height` are all behavior, not noise.
- This should be treated as a parser-spec extraction, not a behavior redesign.
  The refactor is only successful if all current themes produce the same
  runtime values as before.
- Because the theme loader is so localized, it is a better early cleanup than
  the earlier plan suggested.

### Code areas

- `docking/core/theme.py`
- tests in `tests/core/test_theme.py`
- any theme consumers that rely on undocumented fallback behavior

### Acceptance criteria

- one source of truth defines theme fields and defaults
- new theme fields require one spec entry, not repeated parsing branches
- current theme files still load with the same user-visible results

## 7. Make Applet Discovery Truly Lazy

### Current problem

The applet registry promises lazy loading, but package discovery still imports
many applet packages during startup:

- `docking/applets/__init__.py`
- applet package `__init__.py` files

That is costly because package `__init__.py` often re-exports runtime-heavy
symbols or imports GTK code indirectly.

### Required changes

1. Tighten the applet package contract:
   - package `__init__.py` contains only `meta = AppletMeta(...)`
   - no applet class re-export
   - no runtime helper re-export
2. Change class loading to a simple convention:
   - import `docking.applets.<applet_id>.applet`
   - resolve the concrete subclass there
3. Prefer one explicit class name convention to reflective scanning if possible:
   - for example, each `applet.py` exposes `Applet`
   - or each module sets `APPLET_CLASS`
4. If reflective scanning stays, keep it inside `applet.py` only, not package
   discovery.
5. Update scaffolding and applet docs so new applets follow the cheaper
   structure automatically.
6. Add warning-level logging for applet packages that violate the contract when
   that condition means startup cost or broken discovery.

### Unknowns and wrinkles

- Package-level applet imports are used widely in tests and documented as part
  of the applet structure. Making `__init__.py` metadata-only is therefore a
  public import-surface change, not just a startup optimization.
- Several applet modules currently import `meta` from their own package
  namespace. If `__init__.py` becomes metadata-only, the migration should decide
  whether `meta` stays there, moves to a dedicated module, or remains the only
  allowed package export.
- The plan should keep “metadata-only” as the desired steady state, but it
  should not assume every applet can flip there in one pass without a temporary
  compatibility strategy.

### Code areas

- `docking/applets/__init__.py`
- `docking/scaffold.py`
- `docs/APPLETS.md`
- applet package `__init__.py` files that still re-export runtime code
- tests in `tests/applets/test_registry.py`

### Acceptance criteria

- applet catalog creation does not import GTK-heavy applet runtime modules
- startup logs show fast discovery without preloading applet implementation code
- new applets get the cheap package shape by default

## 8. Expand Shared Applet Infrastructure

### Current problem

The base applet contract is intentionally small, but many applets now duplicate
the same infrastructure work:

- GLib timeout/source bookkeeping
- thread or worker lifecycle
- popup anchoring
- overlay windows
- transient loading/error state management

Examples live in:

- `docking/applets/base.py`
- `docking/applets/quote/applet.py`
- `docking/applets/calendar/applet.py`
- `docking/applets/windowkiller/applet.py`
- remote-content applets such as `quote`, `trivia`, `todayinhistory`

### Required changes

1. Add a source registry to the base applet layer:
   - register timeout/idle/source ids
   - cancel them automatically during `stop()`
2. Expand `BackgroundWorker` or add a parallel helper for fetch-style applets so
   network-backed applets do not each re-invent polling and UI handoff.
3. Create shared popup/window helpers:
   - anchored popup
   - transient overlay or fullscreen grab window
4. Create one small state pattern for remote-content applets:
   - idle
   - loading
   - success
   - error
5. Migrate the noisiest repeated applets first:
   - `quote`
   - `todayinhistory`
   - `trivia`
   - popup-heavy applets with near-identical GTK setup

### Unknowns and wrinkles

- The duplication is real, but it is not all one pattern. There are at least
  three different clusters:
  - timer/source lifecycle
  - threaded fetch + GTK handoff
  - popup/overlay window construction
- This means the right move is probably a set of small opt-in helpers, not a
  heavier "applet framework" that every applet must conform to immediately.
- This item gets much more expensive if attempted as a wholesale migration.
  It stays tractable if treated as incremental infrastructure with a few clear
  first adopters.

### Code areas

- `docking/applets/base.py`
- `docking/applets/worker.py`
- applets listed above
- tests in the corresponding `tests/applets/` modules

### Acceptance criteria

- applets stop carrying repeated timer cleanup and popup boilerplate
- lifecycle cleanup is more consistent across applets
- remote-content applets share a common polling/result/error pattern

## 9. Replace Duplicated IPC Method Definitions

### Current problem

The D-Bus contract is described in two places:

- `docking/ipc/introspection.py`
- `docking/ipc/items_service.py`

That makes drift easy. It also hides semantic mismatches, such as `Remove`
really meaning "unpin this item".

### Required changes

1. Introduce one structured IPC method table:
   - method name
   - input signature
   - output signature
   - implementation function
   - public description
2. Generate introspection XML from that table.
3. Dispatch method calls from the same table instead of repeating method names
   in manual branching.
4. Rename misleading methods where the wire contract still allows it:
   - prefer `Unpin`
   - if wire compatibility must be preserved, keep `Remove` as a compatibility
     alias and document it explicitly
5. Separate validation errors from internal failures so logs and D-Bus errors
   become more useful.

### Unknowns and wrinkles

- The real simplification target is the duplicated method metadata between the
  static XML and manual dispatch. Generating XML may help, but it is not the
  goal by itself.
- If the XML stays static for readability, the plan should still centralize
  method signatures and behavior in one structured source and derive the tests
  from that.
- Renaming `Remove` affects the external D-Bus contract. The compatibility story
  needs to be settled before changing method names, because this is a user- and
  automation-facing API rather than an internal helper.

### Code areas

- `docking/ipc/items_service.py`
- `docking/ipc/introspection.py`
- `docs/DBUS.md`
- tests in `tests/ipc/test_items_service.py`

### Acceptance criteria

- IPC contract is defined once
- introspection XML and dispatch behavior cannot drift independently
- method naming better reflects real semantics

## Cross-Cutting Rules For This Refactor Work

- Prefer moving behavior behind narrower interfaces over adding more optional
  flags or callback plumbing.
- Do not add test-only compatibility branches while simplifying contracts.
- Preserve startup performance, especially around applet discovery and weather.
- Preserve warning quality:
  - warn when real functionality is degraded
  - use debug logs for expected skips
  - avoid log spam on hot paths unless messages are deduplicated
- Keep each refactor separately shippable with its own tests.

## Suggested Deliverables

Each simplification item should be delivered as its own change set with:

1. code changes
2. targeted tests
3. architecture/doc updates where the public shape changed
4. a short migration note in the commit message or PR description describing
   what ownership moved and why
